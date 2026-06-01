"""
LINE 美術圖審查工具 — LINE API 封裝
處理圖片下載、訊息回覆
"""
import requests
import config
from pathlib import Path
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def download_image(message_id: str) -> Path:
    """
    從 LINE 下載圖片到本地暫存
    
    Args:
        message_id: LINE 訊息 ID
    
    Returns:
        本地圖片路徑
    """
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    headers = {
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    
    # 以時間戳命名避免衝突
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{message_id}.jpg"
    filepath = config.IMAGES_DIR / filename
    
    filepath.write_bytes(resp.content)
    return filepath


def reply_text(reply_token: str, text: str) -> bool:
    """
    用 Reply API 回覆文字訊息
    
    Args:
        reply_token: LINE 回覆 token（一次性，需立即使用）
        text: 回覆的文字內容（上限 5000 字元）
    
    Returns:
        是否成功
    """
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # LINE 文字訊息上限 5000 字元
    if len(text) > 5000:
        text = text[:4950] + "\n\n⚠️ 回覆過長，已截斷"
    
    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"LINE API reply failed (HTTP {resp.status_code}): {resp.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINE API reply exception: {e}", exc_info=True)
        return False


def push_text(user_id: str, text: str) -> bool:
    """
    用 Push API 主動推送文字訊息（用於非即時回覆場景）
    
    Args:
        user_id: LINE 使用者/群組 ID
        text: 推送的文字內容
    
    Returns:
        是否成功
    """
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    if len(text) > 5000:
        text = text[:4950] + "\n\n⚠️ 回覆過長，已截斷"
    
    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"LINE API push failed (HTTP {resp.status_code}): {resp.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINE API push exception: {e}", exc_info=True)
        return False


def reply_image(reply_token: str, original_url: str, preview_url: str) -> bool:
    """
    用 Reply API 回覆圖片訊息
    """
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "image",
                "originalContentUrl": original_url,
                "previewImageUrl": preview_url
            }
        ]
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"LINE API reply_image failed (HTTP {resp.status_code}): {resp.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINE API reply_image exception: {e}", exc_info=True)
        return False


def push_image(user_id: str, original_url: str, preview_url: str) -> bool:
    """
    用 Push API 推送圖片訊息
    """
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "image",
                "originalContentUrl": original_url,
                "previewImageUrl": preview_url
            }
        ]
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"LINE API push_image failed (HTTP {resp.status_code}): {resp.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINE API push_image exception: {e}", exc_info=True)
        return False


def get_profile(user_id: str) -> dict:
    """取得使用者基本資料"""
    url = f"https://api.line.me/v2/bot/profile/{user_id}"
    headers = {
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code == 200:
        return resp.json()
    return {"displayName": "未知使用者"}
