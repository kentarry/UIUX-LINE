"""
LINE 美術圖審查工具 — 使用者工作階段管理
追蹤每位使用者目前選擇的遊戲，以便載入對應知識庫
"""
import time
import threading
import logging

logger = logging.getLogger(__name__)

# ── 使用者 Session 儲存 ──
# 格式: { user_id: { "game": "明星3缺1", "timestamp": 1234567890.0 } }
_sessions: dict[str, dict] = {}

# ── 待處理圖片暫存 ──
# 格式: { user_id: { "image_path": Path, "message_id": str, "timestamp": float } }
_pending_images: dict[str, dict] = {}
_session_lock = threading.Lock()

# Session 過期時間（秒）— 10 分鐘沒操作就清除（平衡連續作業與資源回收）
SESSION_TIMEOUT = 10 * 60

# ── 支援的遊戲清單 ──
SUPPORTED_GAMES = {
    "明星3缺1": {
        "name": "明星3缺1",
        "knowledge_file": "明星3缺1.md",
        "aliases": ["明星三缺一", "明星3缺一", "明星三缺1", "3缺1", "三缺一"],
    },
    "滿貫大亨": {
        "name": "滿貫大亨",
        "knowledge_file": "滿貫大亨.md",
        "aliases": ["满贯大亨", "滿貫"],
    },
}


def match_game(text: str) -> str | None:
    """
    從使用者文字中辨識遊戲名稱

    Args:
        text: 使用者輸入的文字

    Returns:
        匹配到的遊戲正式名稱，或 None
    """
    text = text.strip()

    for game_name, info in SUPPORTED_GAMES.items():
        # 精確匹配遊戲名
        if game_name in text:
            return game_name

        # 匹配別名
        for alias in info["aliases"]:
            if alias in text:
                return game_name

    return None


def set_game(user_id: str, game_name: str, context: str = ""):
    """設定使用者當前選擇的遊戲與額外上下文"""
    with _session_lock:
        _sessions[user_id] = {
            "game": game_name,
            "context": context,
            "timestamp": time.time(),
        }
    logger.info(f"使用者 {user_id[:8]}... 選擇遊戲: {game_name}")


def get_game(user_id: str) -> tuple[str | None, str]:
    """
    取得使用者當前選擇的遊戲與上下文

    Returns:
        (遊戲名稱或 None, 上下文文字)
    """
    with _session_lock:
        session = _sessions.get(user_id)
        if not session:
            return None, ""

        # 檢查是否過期
        if time.time() - session["timestamp"] > SESSION_TIMEOUT:
            del _sessions[user_id]
            logger.info(f"使用者 {user_id[:8]}... Session 已過期")
            return None, ""

        # 更新活動時間
        session["timestamp"] = time.time()
        return session["game"], session.get("context", "")


def clear_game(user_id: str):
    """清除使用者的遊戲選擇"""
    with _session_lock:
        if user_id in _sessions:
            del _sessions[user_id]


def set_pending_image(user_id: str, image_path, message_id: str):
    """
    暫存使用者的待處理圖片（尚未選擇遊戲時）

    Args:
        user_id: 使用者 ID
        image_path: 已下載的圖片路徑
        message_id: LINE 訊息 ID
    """
    with _session_lock:
        _pending_images[user_id] = {
            "image_path": str(image_path),
            "message_id": message_id,
            "timestamp": time.time(),
        }
    logger.info(f"使用者 {user_id[:8]}... 暫存待處理圖片: {message_id}")


def get_pending_image(user_id: str) -> dict | None:
    """
    取得並清除使用者的待處理圖片

    Returns:
        {"image_path": str, "message_id": str} 或 None
    """
    with _session_lock:
        pending = _pending_images.pop(user_id, None)
        if not pending:
            return None

        # 檢查是否過期（10 分鐘）
        if time.time() - pending["timestamp"] > 10 * 60:
            logger.info(f"使用者 {user_id[:8]}... 待處理圖片已過期")
            return None

        return pending


def has_pending_image(user_id: str) -> bool:
    """檢查使用者是否有待處理圖片"""
    with _session_lock:
        pending = _pending_images.get(user_id)
        if not pending:
            return False
        # 檢查過期
        if time.time() - pending["timestamp"] > 10 * 60:
            del _pending_images[user_id]
            return False
        return True


def clear_pending_image(user_id: str):
    """清除使用者的待處理圖片"""
    with _session_lock:
        _pending_images.pop(user_id, None)


def get_game_info(game_name: str) -> dict | None:
    """取得遊戲的設定資訊"""
    return SUPPORTED_GAMES.get(game_name)


def get_supported_games_text() -> str:
    """產生支援遊戲列表的文字（用於提示使用者）"""
    games = list(SUPPORTED_GAMES.keys())
    return "、".join(games)


def get_active_sessions_count() -> int:
    """取得目前活躍的 session 數量"""
    now = time.time()
    with _session_lock:
        # 同時清理過期的 sessions
        expired = [
            uid for uid, s in _sessions.items()
            if now - s["timestamp"] > SESSION_TIMEOUT
        ]
        for uid in expired:
            del _sessions[uid]

        # 同時清理過期的 pending images（10 分鐘）
        expired_pending = [
            uid for uid, p in _pending_images.items()
            if now - p["timestamp"] > 10 * 60
        ]
        for uid in expired_pending:
            del _pending_images[uid]

        return len(_sessions)


def get_pending_images_count() -> int:
    """取得目前待處理圖片數量"""
    with _session_lock:
        return len(_pending_images)
