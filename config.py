"""
LINE 美術圖審查工具 — 設定檔
所有路徑、API 設定集中管理
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# ── 載入 .env ──
load_dotenv(Path(__file__).parent / ".env")

# ── 專案路徑 ──
BASE_DIR = Path(__file__).parent
SKILLS_DIR = BASE_DIR / "skills"
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
IMAGES_DIR = BASE_DIR / "images"
LOGS_DIR = BASE_DIR / "logs"

# 確保目錄存在
for d in [IMAGES_DIR, LOGS_DIR]:
    d.mkdir(exist_ok=True)

# ── LINE API ──
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

# ── Gemini API（多 Key 輪替支援）──
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# 多 Key 支援：逗號分隔多個 Key（例如 key1,key2,key3）
GOOGLE_API_KEYS = [
    k.strip() for k in os.getenv("GOOGLE_API_KEYS", "").split(",") if k.strip()
]
# 如果沒設定 GOOGLE_API_KEYS，用單一 GOOGLE_API_KEY
if not GOOGLE_API_KEYS and GOOGLE_API_KEY:
    GOOGLE_API_KEYS = [GOOGLE_API_KEY]

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# ── OpenRouter 備援 ──
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "google/gemini-2.0-flash-exp:free"
)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ── 重試設定 ──
RETRY_MAX_ATTEMPTS = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
RETRY_BASE_DELAY = float(os.getenv("RETRY_BASE_DELAY", "2.0"))  # 秒

# ── 圖片處理 ──
IMAGE_MAX_SIZE = 1024  # 壓縮至此長邊 px，減少 vision token
IMAGE_QUALITY = 85     # JPEG 品質
IMAGE_RETAIN_HOURS = 24  # 自動清理超過 N 小時的圖片

# ── Server ──
PORT = int(os.getenv("PORT", 5000))

# ── Skill 檔案 ──
UX_REVIEW_SKILL = SKILLS_DIR / "ux_review.md"
DESIGN_RULES_FILE = KNOWLEDGE_DIR / "design_rules.md"

# ── 擴充知識庫檔案（可選）──
PROJECT_SPECIFIC_FILE = KNOWLEDGE_DIR / "project_specific.md"
COMMON_ISSUES_FILE = KNOWLEDGE_DIR / "common_issues.md"
REVIEW_EXAMPLES_FILE = KNOWLEDGE_DIR / "review_examples.md"


def validate():
    """驗證必要設定，回傳缺少的項目列表"""
    missing = []
    if not LINE_CHANNEL_ACCESS_TOKEN:
        missing.append("LINE_CHANNEL_ACCESS_TOKEN")
    if not LINE_CHANNEL_SECRET:
        missing.append("LINE_CHANNEL_SECRET")
    if not GOOGLE_API_KEYS and not OPENROUTER_API_KEY:
        missing.append("GOOGLE_API_KEY(S) 或 OPENROUTER_API_KEY（至少需要一個）")
    if not UX_REVIEW_SKILL.exists():
        missing.append(f"Skill file: {UX_REVIEW_SKILL}")
    if not DESIGN_RULES_FILE.exists():
        missing.append(f"Knowledge file: {DESIGN_RULES_FILE}")
    return missing
