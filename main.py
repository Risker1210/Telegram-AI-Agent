# main.py (修正版)
import logging
import os, sys
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
    #application.bot_data["aiohttp_session"] = aiohttp.ClientSession()
    #logging.info("aiohttp.ClientSession 已建立並注入 bot_data。")
    application.aiohttp_session = aiohttp.ClientSession()
    logging.info("aiohttp.ClientSession 已建立並直接附加到 application 物件。")


async def post_shutdown(application: Application):
    """應用程式關閉前執行的非同步函式"""
    # ✨ 從 application 的屬性中取得 session
    if hasattr(application, 'aiohttp_session') and not application.aiohttp_session.closed:
        await application.aiohttp_session.close()
        logging.info("aiohttp.ClientSession 已關閉。")
    # ✨ 在關閉時明確地儲存一次資料，並印出日誌
    logging.info("正在執行 post_shutdown，嘗試儲存持久化資料...")
    await application.update_persistence()
    logging.info("持久化資料儲存完畢。")

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

    # (持久化設定的程式碼)
    persistence_filepath = "data/lala_bot_data.pkl"
    persistence_dir = os.path.dirname(persistence_filepath)
    if persistence_dir:
        os.makedirs(persistence_dir, exist_ok=True)
    logging.info(f"持久化檔案將儲存於: {os.path.abspath(persistence_filepath)}")
    persistence = PicklePersistence(filepath=persistence_filepath)



    # 建立 Application
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ✨ 建立 BotCommands 的實例
    commands = BotCommands()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", commands.reset))
    app.add_handler(CommandHandler("model", commands.set_model))

    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    logging.info("Bot 開始輪詢 (模組化 + 函式呼叫 + 持久化模式)...")
    app.run_polling()

if __name__ == "__main__":
    main()