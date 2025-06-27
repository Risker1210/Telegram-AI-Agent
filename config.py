# config.py
import os
from dotenv import load_dotenv

def load_env():
    """載入並驗證環境變數"""
    load_dotenv()
    env_vars = {
        "BOT_TOKEN": os.getenv("BOT_TOKEN"),
        "NEWS_API_KEY": os.getenv("NEWS_API_KEY"),
        "WEATHER_API_KEY": os.getenv("WEATHER_API_KEY"),
        "WEATHER_CITY": os.getenv("WEATHER_CITY", "Taipei"),
        "OLLAMA_BASE_URL": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/api"),
        "OLLAMA_MODEL": os.getenv("OLLAMA_MODEL", "qwen2:7b"),
        "OLLAMA_VISION_MODEL": os.getenv("OLLAMA_VISION_MODEL", "llava:latest"),
        "DEFAULT_PERSONA": os.getenv("DEFAULT_PERSONA")
    }
    required = ["BOT_TOKEN", "OLLAMA_VISION_MODEL", "WEATHER_API_KEY", "NEWS_API_KEY", "DEFAULT_PERSONA"]
    missing = [key for key in required if not env_vars[key]]
    if missing:
        raise SystemExit(f"錯誤：請在 .env 中設定以下變數：{', '.join(missing)}")
    return env_vars

# --- 載入設定 ---
env = load_env()

# --- 匯出常數 ---
BOT_TOKEN = env["BOT_TOKEN"]
NEWS_API_KEY = env["NEWS_API_KEY"]
WEATHER_API_KEY = env["WEATHER_API_KEY"]
WEATHER_CITY = env["WEATHER_CITY"]
OLLAMA_BASE_URL = env["OLLAMA_BASE_URL"]
DEFAULT_MODEL = env["OLLAMA_MODEL"]
OLLAMA_VISION_MODEL = env["OLLAMA_VISION_MODEL"]
DEFAULT_PERSONA = env["DEFAULT_PERSONA"]

MAX_ROUNDS = 12
IMAGE_SIZE_LIMIT = 5 * 1024 * 1024
PHOTO_PROMPT = "（專注地看著你分享的照片）哇，這張照片……"
STICKER_PROMPT = "（看到你傳來的貼圖，溫柔地微笑著）這張貼圖真有趣，它讓我想到了……"