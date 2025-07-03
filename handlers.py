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
from config import *
from utils import (ask_ollama_once, ask_ollama_stream, stream_and_edit_message, 
                   reply_safely, image_to_base64, extract_json_from_text, filter_stream)
from tools import TOOL_REGISTRY, Tools

# --- 主要對話處理 ---
async def chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    處理文字訊息的最終版 ReAct 循環，採用簡化的工具箱和強化的工作流。
    """
    user_msg = update.message.text
    user_id = update.effective_user.id
    logging.info(f"收到使用者 {user_id} 的訊息：{user_msg[:80]}...")
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    # 準備對話歷史與 Persona
    hist = ctx.user_data.setdefault("history", deque(maxlen=MAX_ROUNDS))
    persona = ctx.user_data.get("persona", DEFAULT_PERSONA)
    first_step_msgs = [{"role": "system", "content": persona}] + list(hist) + [{"role": "user", "content": user_msg}]
    
    # 步驟一：呼叫模型，獲取初步回應
    model_response = await ask_ollama_once(ctx, first_step_msgs)
    logging.info(f"模型初步回應原文: {model_response}")

    # 步驟二：從回應中提取並解析工具請求
    json_str = extract_json_from_text(model_response)
    tool_calls = []
    if json_str:
        try:
            parsed_response = ast.literal_eval(json_str.replace("'", '"'))
            if isinstance(parsed_response, list):
                tool_calls = parsed_response
            elif isinstance(parsed_response, dict):
                tool_calls.append(parsed_response)
        except Exception as e:
            logging.error(f"最終解析失敗 ({e})，放棄工具呼叫。")

    final_reply = ""
    
    # 步驟三：核心決策 -> 根據有無工具請求，進入不同分支
    if isinstance(tool_calls, list) and tool_calls and all(isinstance(call, dict) and "tool_name" in call for call in tool_calls):
        
        logging.info(f"偵測到有效工具請求: {[call.get('tool_name') for call in tool_calls]}")
        
        # --- ✨ V20 核心改動：不再有 is_briefing_task 的特殊判斷 ---
        # --- 所有工具都走標準的 ReAct 循環 ---
        
        tool_results, tasks_to_run, async_calls_map = [], [], []
        for call in tool_calls:
            tool_name = call.get("tool_name")
            if tool_name not in TOOL_REGISTRY:
                logging.warning(f"請求了未註冊的工具: {tool_name}")
                continue

            logging.info(f"執行工具: {tool_name}, 參數: {call.get('arguments', {})}")
            tool_function = TOOL_REGISTRY[tool_name]
            arguments = call.get("arguments", {})
            kwargs = arguments.copy()

            if asyncio.iscoroutinefunction(tool_function):
                if tool_name == 'get_current_weather': 
                    kwargs['ctx'] = ctx
                    kwargs.setdefault('city', WEATHER_CITY)
                tasks_to_run.append(tool_function(**kwargs))
                async_calls_map.append(call)
            else:
                if tool_name in ['add_todo', 'list_todos', 'get_news_headlines']: 
                    kwargs['ctx'] = ctx
                result = tool_function(**kwargs)
                tool_results.append({"tool_call": call, "result": result})

        if tasks_to_run:
            async_results = await asyncio.gather(*tasks_to_run)
            for call, result in zip(async_calls_map, async_results):
                tool_results.append({"tool_call": call, "result": result})

        second_step_msgs = first_step_msgs
        for res in tool_results:
            tool_name = res['tool_call'].get('tool_name')
            tool_result_str = str(res['result'])
            logging.info(f"工具 {tool_name} 執行結果: {tool_result_str[:150]}...")
            
            # ✨ V20 核心改動：為新聞工具建立專屬的「最終烹飪指令」
            final_instruction = ""
            if tool_name == 'get_news_headlines':
                final_instruction = (
                    "工具「get_news_headlines」已經執行完成，以下是它回傳的原始新聞文章列表（JSON 格式）。\n"
                    "請你扮演一位專業又有趣的新聞主播，完成以下任務：\n"
                    "1. 閱讀所有文章，篩選出 2-3 則最重要或最有趣的新聞。\n"
                    "2. 為每一則選出的新聞，寫一段精簡又有吸引力的摘要。\n"
                    "3. 按照重要性排序，將摘要整理成一份新聞簡報。\n"
                    "4. 最後，以 Lala 的口吻報導這份簡報，並務必在每則新聞的結尾附上原始的 `url` 連結。\n\n"
                    f"【原始新聞數據】\n---\n{tool_result_str}\n---"
                )
            else:
                # 對於其他工具，使用我們之前設計的通用指令
                final_instruction = (
                    f"工具「{tool_name}」已經執行完成，以下是它的原始回傳數據。\n"
                    "請你**務必**基於這些數據，而不是你自己的內部知識，"
                    "以 Lala 的口吻和風格，生成一段自然的、口語化的、完整的對話來回覆使用者。\n\n"
                    f"【工具數據】\n---\n{tool_result_str}\n---"
                )

            second_step_msgs.extend([
                {"role": "assistant", "content": json.dumps(res['tool_call'], ensure_ascii=False)},
                {"role": "tool", "content": final_instruction}
            ])
        
        raw_generator = ask_ollama_stream(ctx, second_step_msgs)
        filtered_generator = filter_stream(raw_generator)
        final_reply = await stream_and_edit_message(update, ctx, filtered_generator)

    else:
        # --- 分支 C：普通聊天 ---
        logging.info("未偵測到有效工具請求，當作一般對話處理。")
        async def text_generator(text):
            cleaned_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            for i in range(0, len(cleaned_text), 10):
                yield cleaned_text[i:i+10]
                await asyncio.sleep(0.01)
        
        response_generator = text_generator(model_response)
        final_reply = await stream_and_edit_message(update, ctx, response_generator)
    
    # --- 步驟四：儲存最終歷史紀錄 ---
    if not final_reply: 
        final_reply = "（我好像有點不知道該說什麼了...）"
    
    hist.append({"role": "user", "content": user_msg})
    hist.append({"role": "assistant", "content": final_reply})

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
    
    if file_size and file_size > settings.IMAGE_SIZE_LIMIT: await update.message.reply_text(f"⚠️ 檔案過大"); return
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        tg_file = await ctx.bot.get_file(file_id)
        with io.BytesIO() as bio:
            await tg_file.download_to_memory(out=bio); bio.seek(0)
            image_b64 = image_to_base64(bio.getvalue())
        
        hist = ctx.user_data.setdefault("history", deque(maxlen=settings.MAX_ROUNDS))
        persona = ctx.user_data.get("persona", settings.DEFAULT_PERSONA)
        prompt = settings.STICKER_PROMPT if is_sticker else settings.PHOTO_PROMPT
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
            current_model = ctx.user_data.get("model", settings.DEFAULT_MODEL)
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