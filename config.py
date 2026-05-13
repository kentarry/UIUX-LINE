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

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

# 已下架模型自動修正（防止 404 NOT_FOUND）
_DEPRECATED_MODELS = {
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-1.0-pro",
    "gemini-pro",
    "gemini-pro-vision",
}
if GEMINI_MODEL in _DEPRECATED_MODELS:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        f"模型 '{GEMINI_MODEL}' 已被 Google 下架，自動切換為 'gemini-2.5-pro'"
    )
    GEMINI_MODEL = "gemini-2.5-pro"

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
IMAGE_MAX_SIZE = 768   # 壓縮至此長邊 px（768 足夠 UI 審查，省 ~30% vision token）
IMAGE_QUALITY = 75     # JPEG 品質（75 對 UI 審查已足夠）
IMAGE_RETAIN_HOURS = 24  # 自動清理超過 N 小時的圖片

# ── Server ──
PORT = int(os.getenv("PORT", 5000))

# ── Skill 檔案 ──
UX_REVIEW_SKILL = SKILLS_DIR / "ux_review.md"
DESIGN_RULES_FILE = KNOWLEDGE_DIR / "design_rules.md"

# ── 擴充知識庫檔案（可選）──
# common_issues 和 review_examples 已整合至 design_rules.md
PROJECT_SPECIFIC_FILE = KNOWLEDGE_DIR / "project_specific.md"

# ── NotebookLM 設定 ──
# UIUX 設計思維筆記本（通用設計知識，所有遊戲共用）
NOTEBOOKLM_UIUX_URL = os.getenv(
    "NOTEBOOKLM_UIUX_URL",
    "https://notebooklm.google.com/notebook/b11362de-e39b-4189-96e6-e557b854b137?authuser=6"
)
# 遊戲專屬筆記本（可擴充）
NOTEBOOKLM_NOTEBOOKS = {
    "uiux_design": {
        "name": "UIUX設計思維",
        "url": NOTEBOOKLM_UIUX_URL,
        "target_file": "design_rules.md",
        "description": "通用 UI/UX 設計審查知識（22 個設計資源來源）",
    },
}


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
