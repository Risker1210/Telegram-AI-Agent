# utils.py (V5 最終版)
import logging
import base64
import aiohttp
import telegram
import asyncio
import json
import re
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest

# 從 config 模組匯入我們需要的設定
from config import OLLAMA_BASE_URL, OLLAMA_VISION_MODEL, DEFAULT_MODEL

def image_to_base64(raw: bytes):
    return base64.b64encode(raw).decode()

# ✨ V6 核心改造：ask_ollama 現在是一個強大的非同步產生器
async def ask_ollama_stream(ctx: ContextTypes.DEFAULT_TYPE, msgs: list, image_b64: str = None):
    """
    向 Ollama API 發送請求並以串流方式獲取回應。
    這是一個非同步產生器，會逐一產出 (yield) 收到的文字片段。
    """
    session: aiohttp.ClientSession = ctx.application.aiohttp_session
    
    if image_b64:
        model_to_use = OLLAMA_VISION_MODEL
    else:
        model_to_use = ctx.user_data.get("model", DEFAULT_MODEL)

    payload = {
        "model": model_to_use,
        "messages": msgs,
        "stream": True, # ✨ 開啟串流模式
        "options": { "temperature": 0.5 }
    }
    if image_b64: payload["images"] = [image_b64]

    url = f"{OLLAMA_BASE_URL}/chat"
    try:
        async with session.post(url, json=payload) as response:
            if response.status != 200:
                error_body = await response.text()
                logging.error(f"Ollama API 錯誤: {response.status}, {error_body}")
                yield "⚠️ API 回應錯誤"
                return

            # 逐行讀取串流回應
            async for line in response.content:
                if line:
                    try:
                        chunk = json.loads(line.decode('utf-8'))
                        if chunk.get("done") is False:
                            content = chunk.get("message", {}).get("content", "")
                            yield content
                    except json.JSONDecodeError:
                        logging.warning(f"無法解析的 JSON 片段: {line}")
                        continue
    except Exception as e:
        logging.exception(f"ask_ollama_stream 未知錯誤: {e}")
        yield f"⚠️ 發生未知錯誤"

# ✨ V6 新增：一個非串流的版本，供內部工具判斷使用
async def ask_ollama_once(
    ctx: ContextTypes.DEFAULT_TYPE, msgs: list, image_b64: str | None = None
) -> str:
    """
    向 Ollama API 發送一次性請求，獲取完整回應。
    主要用於需要完整 JSON 的函式呼叫判斷。
    """
    full_response = ""
    async for chunk in ask_ollama_stream(ctx, msgs, image_b64=image_b64):
        full_response += chunk
    return full_response


# ✨ V6 新增：優雅的訊息編輯器，實現打字效果
async def stream_and_edit_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE, response_generator):
    """
    V2: 接收一個回應產生器，實現串流打字效果，並修復重複發送的 Bug。
    """
    message_to_edit = None
    full_response = ""
    buffer = ""
    last_edit_time = 0
    edit_success = True # ✨ 新增一個旗標來追蹤編輯狀態

    try:
        # 第一次一定先發送訊息
        message_to_edit = await update.message.reply_text("...")

        async for chunk in response_generator:
            buffer += chunk
            current_time = asyncio.get_event_loop().time()

            if current_time - last_edit_time > 0.75 and buffer: # 稍微拉長編輯間隔
                try:
                    await ctx.bot.edit_message_text(
                        text=buffer, 
                        chat_id=message_to_edit.chat_id, 
                        message_id=message_to_edit.message_id
                    )
                    last_edit_time = current_time
                except BadRequest as e:
                    # 如果是因為訊息未修改而報錯，就忽略它，這是正常現象
                    if "Message is not modified" not in str(e):
                        raise e # 如果是其他錯誤，就拋出讓外層捕捉
        
        # 串流結束後，確保最後的完整內容被更新
        full_response = buffer
        if message_to_edit.text != full_response:
            await ctx.bot.edit_message_text(
                text=full_response, 
                chat_id=message_to_edit.chat_id, 
                message_id=message_to_edit.message_id
            )

    except (Forbidden, BadRequest) as e:
        logging.warning(f"編輯訊息時發生錯誤，將直接發送最終結果: {e}")
        edit_success = False # ✨ 標記編輯失敗
    
    # ✨ 如果在整個編輯過程中發生了無法忽略的錯誤，
    # 且我們有最終的完整回覆，就用 reply_safely 發送一次作為補救。
    if not edit_success and full_response:
        await reply_safely(update, full_response)
    # ✨ 如果編輯過程是成功的，就不再需要做任何事。

    # 回傳完整的訊息，以便存入歷史
    # 如果 full_response 是空的（例如，產生器完全沒東西），就用 buffer 的內容
    return full_response or buffer

async def filter_stream(generator):
    """
    一個非同步產生器，接收另一個產生器，
    並在產出內容前過濾掉 <think>...</think> 區塊。
    """
    full_response = ""
    async for chunk in generator:
        full_response += chunk

    # 等到整個回應都接收完畢後，再一次性過濾
    # 這樣可以避免 <think> 標籤被切斷導致過濾失敗
    cleaned_response = re.sub(r'<think>.*?</think>', '', full_response, flags=re.DOTALL).strip()
    
    # 將過濾後的乾淨文字，重新變成一個產生器並回傳
    # 這裡我們用一個簡單的方式模擬串流，讓打字效果依然存在
    for i in range(0, len(cleaned_response), 10):
        yield cleaned_response[i:i+10]
        await asyncio.sleep(0.01)


async def reply_safely(update: Update, text: str):
    """安全地回覆訊息，優先嘗試 MarkdownV2，失敗則改用純文字"""
    if not text or not text.strip():
        logging.warning("嘗試發送空訊息，已略過。")
        return
    try:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
    except telegram.error.BadRequest as e:
        if "can't parse entities" in str(e).lower():
            logging.warning(f"MarkdownV2 解析失敗 ({e})，自動改用純文字。")
            try:
                await update.message.reply_text(text)
            except Exception as final_e:
                logging.error(f"純文字模式發送也失敗: {final_e}")
                await update.message.reply_text("抱歉，我好像說錯話了 >.<")
        else:
            logging.error(f"非預期的 BadRequest: {e}")
            await update.message.reply_text("抱歉，回覆時發生問題。")

# ✨ 使用您提出的、最健壯的 V4 版本，並修正單引號問題
def extract_json_from_text(text: str) -> str | None:
    """
    V4.1: 使用 json.JSONDecoder.raw_decode 逐字掃描，並預處理單引號問題。
    """
    text_no_think = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    
    # 預處理：將 Python 風格的單引號替換為 JSON 標準的雙引號
    # 為了避免錯誤替換字串內容中的單引號，這是一個簡化的權衡
    # 更複雜的作法需要完整的 Python AST 解析器
    text_to_decode = text_no_think.replace("'", '"')

    decoder = json.JSONDecoder()
    i, n = 0, len(text_to_decode)
    slices: list[str] = []

    while i < n:
        ch = text_to_decode[i]
        if ch in '{[':
            try:
                obj, end = decoder.raw_decode(text_to_decode[i:])
                slices.append(text_to_decode[i : i + end])
                i += end
            except json.JSONDecodeError:
                i += 1
                continue
        else:
            i += 1

    if not slices:
        return None
    if len(slices) == 1:
        return slices[0]
    return '[' + ','.join(slices) + ']'