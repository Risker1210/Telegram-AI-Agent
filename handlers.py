# handlers.py
import logging
import ast
import io
import re
import json
import asyncio
from collections import deque
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import ContextTypes

# 從我們自己的模組中匯入需要的東西
from config import (DEFAULT_MODEL, DEFAULT_PERSONA, IMAGE_SIZE_LIMIT, 
                    MAX_ROUNDS, PHOTO_PROMPT, STICKER_PROMPT, WEATHER_CITY)
from utils import (ask_ollama_once, ask_ollama_stream, stream_and_edit_message, 
                   reply_safely, image_to_base64, extract_json_from_text, filter_stream)
from tools import TOOL_REGISTRY, Tools

# --- 主要對話處理 ---
async def chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    處理文字訊息，實現函式呼叫，並以串流方式回覆 (V10 完整版)
    """
    user_msg = update.message.text
    user_id = update.effective_user.id
    logging.info(f"收到使用者 {user_id} 的訊息：{user_msg[:80]}...")
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    hist = ctx.user_data.setdefault("history", deque(maxlen=MAX_ROUNDS))
    persona = ctx.user_data.get("persona", DEFAULT_PERSONA)
    first_step_msgs = [{"role": "system", "content": persona}] + list(hist) + [{"role": "user", "content": user_msg}]
    
    # --- 步驟一：使用非串流模式進行工具判斷 ---
    model_response = await ask_ollama_once(ctx, first_step_msgs)
    logging.info(f"模型初步回應原文: {model_response}")

    json_str = extract_json_from_text(model_response)
    tool_calls = []
    
    if json_str:
        try:
            # 使用 ast.literal_eval 來處理單引號和雙引號的 JSON/Python Dict
            parsed_response = ast.literal_eval(json_str)
            if isinstance(parsed_response, list):
                tool_calls = parsed_response
            elif isinstance(parsed_response, dict):
                tool_calls.append(parsed_response)
        except Exception as e:
            logging.error(f"最終解析失敗 ({e})，放棄工具呼叫。")

    final_reply = ""
    if isinstance(tool_calls, list) and tool_calls and all(isinstance(call, dict) and "tool_name" in call for call in tool_calls):
        # --- 步驟二：執行所有被請求的工具 ---
        tool_results = []
        tasks_to_run = []
        async_calls_map = [] 

        for call in tool_calls:
            tool_name = call.get("tool_name")
            if tool_name not in TOOL_REGISTRY:
                logging.warning(f"模型請求了未註冊的工具: {tool_name}")
                continue

            logging.info(f"模型請求使用工具: {tool_name}, 參數: {call.get('arguments', {})}")
            tool_function = TOOL_REGISTRY[tool_name]
            arguments = call.get("arguments", {})
            
            kwargs = arguments.copy()
            if asyncio.iscoroutinefunction(tool_function):
                if tool_name == 'get_current_weather': 
                    kwargs['session'] = ctx.bot_data['aiohttp_session']
                    kwargs.setdefault('city', WEATHER_CITY)
                tasks_to_run.append(tool_function(**kwargs))
                async_calls_map.append(call)
            else:
                if tool_name in ['add_todo', 'list_todos']: 
                    kwargs['ctx'] = ctx
                result = tool_function(**kwargs)
                tool_results.append({"tool_call": call, "result": result})

        if tasks_to_run:
            async_results = await asyncio.gather(*tasks_to_run)
            for call, result in zip(async_calls_map, async_results):
                tool_results.append({"tool_call": call, "result": result})

        # --- 步驟三：使用串流模式生成最終回覆 ---
        second_step_msgs = first_step_msgs
        for res in tool_results:
            logging.info(f"工具 {res['tool_call'].get('tool_name')} 執行結果: {str(res['result'])[:150]}...")
            second_step_msgs.extend([
                {"role": "assistant", "content": json.dumps(res['tool_call'], ensure_ascii=False)},
                {"role": "tool", "content": str(res['result'])}
            ])
        # --- ✨ 步驟三：使用串流模式生成最終回覆 (加入過濾) ---
        raw_generator = ask_ollama_stream(ctx, second_step_msgs)
        # 將原始產生器用 filter_stream 包裝起來
        filtered_generator = filter_stream(raw_generator) 
        final_reply = await stream_and_edit_message(update, ctx, filtered_generator)
    else:
        # --- 不需要工具，直接以串流模式回覆 ---
        # 因為 model_response 是完整的，我們要把它變成一個假的非同步產生器
        cleaned_response = re.sub(r'<think>.*?</think>\s*', '', model_response, flags=re.DOTALL).strip()
        
        async def text_generator(text):
            # 模擬串流，將文字分塊產出
            for i in range(0, len(text), 10): # 每 10 個字元為一塊
                yield text[i:i+10]
                await asyncio.sleep(0.01) # 模擬網路延遲，讓打字效果更明顯
        
        response_generator = text_generator(cleaned_response)
        final_reply = await stream_and_edit_message(update, ctx, response_generator)
    
    # 將最終的完整回覆存入歷史
    #cleaned_reply = re.sub(r'<think>.*?</think>\s*', '', final_reply, flags=re.DOTALL).strip()
    cleaned_reply = final_reply 
    if not cleaned_reply:
        cleaned_reply = "（我好像有點不知道該說什麼了...）"
    
    hist.append({"role": "user", "content": user_msg})
    hist.append({"role": "assistant", "content": cleaned_reply})

# --- 圖片/貼圖處理 ---
async def photo_or_sticker_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE, is_sticker: bool):
    file_id, file_size = (None, None)
    if is_sticker:
        sticker = update.message.sticker
        if sticker.is_animated or sticker.is_video: await update.message.reply_text("抱歉，我不支援動態或影片貼圖喔～"); return
        file_id, file_size = sticker.file_id, sticker.file_size
    else:
        photo = update.message.photo[-1]
        file_id, file_size = photo.file_id, photo.file_size
    
    if file_size and file_size > IMAGE_SIZE_LIMIT: await update.message.reply_text(f"⚠️ 檔案過大"); return
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        tg_file = await ctx.bot.get_file(file_id)
        with io.BytesIO() as bio:
            await tg_file.download_to_memory(out=bio); bio.seek(0)
            image_b64 = image_to_base64(bio.getvalue())
        
        hist = ctx.user_data.setdefault("history", deque(maxlen=MAX_ROUNDS))
        persona = ctx.user_data.get("persona", DEFAULT_PERSONA)
        prompt = STICKER_PROMPT if is_sticker else PHOTO_PROMPT
        user_msg_for_hist = "(傳送了一張貼圖)" if is_sticker else "(傳送了一張照片)"
        
        msgs = [{"role": "system", "content": persona}] + list(hist) + [{"role": "user", "content": prompt}]
        reply = await ask_ollama_once(ctx, msgs, image_b64=image_b64)
        cleaned_reply = re.sub(r'<think>.*?</think>\s*', '', reply, flags=re.DOTALL).strip()
        
        hist.append({"role": "user", "content": user_msg_for_hist})
        hist.append({"role": "assistant", "content": cleaned_reply})
        await reply_safely(update, cleaned_reply)
    except Exception as e:
        logging.error(f"處理圖片/貼圖時出錯: {e}"); await update.message.reply_text("⚠️ 處理圖片時發生錯誤。")

async def photo_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE): await photo_or_sticker_handler(update, ctx, is_sticker=False)
async def sticker_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE): await photo_or_sticker_handler(update, ctx, is_sticker=True)


# --- 指令處理 ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    welcome_message = (f"哈囉，{user_name}～ 你好呀！🖐️\n我是你的私人助手 Lala ：）\n\n"
                     "你可以隨時開始跟我聊天、傳照片或貼圖，或者要我幫你查天氣、找資料、看新聞都可以喔！\n\n"
                     "如果需要重設我們的對話，可以輸入 `/reset`。")
    await update.message.reply_text(welcome_message)

class BotCommands:
    @staticmethod
    async def set_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not ctx.args:
            current_model = ctx.user_data.get("model", DEFAULT_MODEL)
            await update.message.reply_text(f"目前模型：`{current_model}`\n用法：`/model llama3:8b`", parse_mode=ParseMode.MARKDOWN)
            return
        model_name = ctx.args[0]
        ctx.user_data["model"] = model_name
        await update.message.reply_text(f"✅ 模型已切換為 `{model_name}`", parse_mode=ParseMode.MARKDOWN)
    
    @staticmethod
    async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        ctx.user_data.clear()
        await update.message.reply_text("🗑️ 已清空您的個人對話歷史、模型和 persona 設定。")

    # ... 您可以將 list_models 和 set_persona 也放在這裡 ...