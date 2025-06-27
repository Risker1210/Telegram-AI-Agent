# main.py (修正版)
import logging
import asyncio
import aiohttp
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, PicklePersistence
)

# 從我們自己的模組中匯入所有需要的東西
from config import BOT_TOKEN, DEFAULT_PERSONA
from handlers import (
    start, chat, photo_handler, sticker_handler, BotCommands
)
from tools import TOOL_REGISTRY, Tools # 雖然 main 不直接用，但 import 進來確保模組可被找到

# --- 應用程式生命週期函式 ---
async def post_init(application: Application):
    """應用程式啟動後執行的非同步函式"""
    application.bot_data["aiohttp_session"] = aiohttp.ClientSession()
    logging.info("aiohttp.ClientSession 已建立並注入 bot_data。")

async def post_shutdown(application: Application):
    """應用程式關閉前執行的非同步函式"""
    session = application.bot_data.get("aiohttp_session")
    if session:
        await session.close()
        logging.info("aiohttp.ClientSession 已關閉。")

def main():
    """主函式，設定並運行機器人"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    
    # 提醒使用者檢查 Persona 設定
    if not DEFAULT_PERSONA or "tool_name" not in DEFAULT_PERSONA:
        print("\n" + "="*50)
        print("⚠️ 警告：您的 .env 檔案中缺少 DEFAULT_PERSONA，")
        print("或 Persona 內容不包含函式呼叫的指示。")
        print("請確認您已將最新的『工具人 Persona』複製到 .env 檔案中！")
        print("="*50 + "\n")

    # 建立 Application
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ✨ 建立 BotCommands 的實例
    commands = BotCommands()
    
    # ✨ 註冊所有指令處理器
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", commands.reset))
    app.add_handler(CommandHandler("model", commands.set_model))
    # 這裡假設您會把 list_models 和 set_persona 從舊檔案移到 handlers.py 的 BotCommands 類別中
    # 如果還沒移，請記得把它們加進去
    # app.add_handler(CommandHandler("models", commands.list_models)) 
    # app.add_handler(CommandHandler("persona", commands.set_persona))

    # 註冊訊息處理器
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    logging.info("Bot 開始輪詢 (模組化 + 函式呼叫模式)...")
    app.run_polling()

if __name__ == "__main__":
    main()