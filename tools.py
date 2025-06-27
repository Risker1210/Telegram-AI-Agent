# tools.py
import logging
from datetime import datetime
import pytz
import aiohttp
from duckduckgo_search import DDGS
from newsapi import NewsApiClient
from telegram.ext import ContextTypes

# 從 config 模組匯入我們需要的設定
from config import NEWS_API_KEY, WEATHER_API_KEY

class Tools:
    CITY_MAP = {
        "台北": "Taipei",
        "桃園": "Taoyuan",
        "台中": "Taichung",
        "台南": "Tainan",
        "高雄": "Kaohsiung",
        "洛杉磯": "Los Angeles",
        "東京": "Tokyo",
    }
    @staticmethod
    def get_current_time():
        tz = pytz.timezone('Asia/Taipei')
        return datetime.now(tz).strftime("%Y年%m月%d日 %A %H:%M")

    @staticmethod
    async def get_current_weather(session: aiohttp.ClientSession, city: str):
        # ✨ 在查詢前，先嘗試轉換成英文名
        city_en = Tools.CITY_MAP.get(city, city) # 如果在字典裡，就用英文名；否則用原文
        
        if not WEATHER_API_KEY: return "（天氣功能未設定 API Key）"
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city_en}&appid={WEATHER_API_KEY}&units=metric&lang=zh_tw"
        try:
            async with session.get(url) as r:
                data = await r.json()
                if r.status == 200: 
                    description = data['weather'][0]['description']
                    temp = data['main']['temp']
                    return f"{city}的天氣是「{description}」，氣溫 {temp}°C。" # 回覆時仍然使用使用者說的中文名
                else: 
                    error_message = data.get('message', '未知錯誤')
                    logging.error(f"天氣 API 錯誤 ({r.status}) for city '{city_en}': {error_message}")
                    return f"（無法取得 {city} 的天氣資訊：{error_message}）"
        except Exception as e: 
            logging.error(f"獲取天氣失敗: {e}")
            return "（獲取天氣時發生網路或解析錯誤）"
        
    @staticmethod
    def search_web(query: str):
        try:
            with DDGS() as ddgs: results = [r for r in ddgs.text(query, max_results=3)]
            if not results: return "抱歉，網路上找不到相關資訊。"
            formatted = [f"標題: {r['title']}\n摘要: {r.get('body', '')}\n---" for r in results]
            return "\n".join(formatted)
        except Exception as e: logging.error(f"網路搜尋失敗: {e}"); return "抱歉，搜尋時發生錯誤。"

    @staticmethod
    def get_news_headlines(query: str = None, category: str = None):
        if not NEWS_API_KEY: return "抱歉，新聞功能未設定 API Key。"
        try:
            newsapi = NewsApiClient(api_key=NEWS_API_KEY)
            if query: headlines = newsapi.get_everything(q=query, language='zh', sort_by='relevancy', page_size=3)
            else: headlines = newsapi.get_top_headlines(category=category, language='zh', country='tw', page_size=3)
            articles = headlines.get('articles', [])
            if not articles: return "找不到相關新聞。"
            formatted = [f"標題: {a['title']}\n摘要: {a.get('description', '')}\n---" for a in articles]
            return "\n".join(formatted)
        except Exception as e: logging.error(f"獲取新聞失敗: {e}"); return "抱歉，查詢新聞時發生錯誤。"

    @staticmethod
    def add_todo(ctx: ContextTypes.DEFAULT_TYPE, item: str):
        todos = ctx.user_data.setdefault("todos", [])
        todos.append(item)
        return f"好的，已經幫你記下待辦事項：『{item}』"

    @staticmethod
    def list_todos(ctx: ContextTypes.DEFAULT_TYPE):
        todos = ctx.user_data.get("todos", [])
        if not todos: return "你的待辦清單現在是空的喔！"
        items = [f"{i}. {item}" for i, item in enumerate(todos, 1)]
        return "這是你目前的待辦清單：\n" + "\n".join(items)

    @staticmethod
    def recommend_music(mood: str):
        if mood in ["傷心", "難過", "失落"]: return "推薦音樂類型：溫柔的鋼琴曲或 Lo-Fi 音樂。"
        elif mood in ["開心", "興奮", "有活力"]: return "推薦音樂類型：節奏感強的 Funk 或電子舞曲。"
        else: return "推薦音樂類型：輕柔的爵士樂或 Bossa Nova。"

# 工具註冊表，將字串名稱映射到函式
TOOL_REGISTRY = {
    "get_current_time": Tools.get_current_time,
    "get_current_weather": Tools.get_current_weather,
    "search_web": Tools.search_web,
    "get_news_headlines": Tools.get_news_headlines,
    "add_todo": Tools.add_todo,
    "list_todos": Tools.list_todos,
    "recommend_music": Tools.recommend_music,
}