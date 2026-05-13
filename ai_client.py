"""
LINE 美術圖審查工具 — AI 客戶端
統一管理 Gemini 多 Key 輪替、指數退避重試、OpenRouter 備援
"""
import config
import time
import base64
import logging
import requests
import threading
from google import genai
from PIL import Image
from io import BytesIO

logger = logging.getLogger(__name__)

# ── Key 輪替管理 ──
_key_index = 0
_key_lock = threading.Lock()
# 記錄每個 key 的冷卻時間（429 後暫停使用）
_key_cooldowns: dict[int, float] = {}


def _get_next_key() -> tuple[int, str]:
    """
    取得下一個可用的 Gemini API Key（執行緒安全）

    Returns:
        (key_index, api_key) 或 (-1, "") 如果全部冷卻中
    """
    global _key_index

    keys = config.GOOGLE_API_KEYS
    if not keys:
        return -1, ""

    now = time.time()

    with _key_lock:
        # 嘗試所有 key
        for _ in range(len(keys)):
            idx = _key_index % len(keys)
            _key_index = (_key_index + 1) % len(keys)

            # 檢查是否在冷卻中
            cooldown_until = _key_cooldowns.get(idx, 0)
            if now >= cooldown_until:
                return idx, keys[idx]

        # 全部冷卻中，回傳冷卻時間最短的
        earliest_idx = min(_key_cooldowns, key=_key_cooldowns.get)
        wait_time = _key_cooldowns[earliest_idx] - now
        if wait_time > 0:
            logger.warning(f"所有 Key 冷卻中，最快可用需等 {wait_time:.1f} 秒")
        return earliest_idx, keys[earliest_idx]


def _mark_key_exhausted(key_index: int, cooldown_seconds: float = 60.0):
    """標記 Key 為配額耗盡，設定冷卻時間"""
    with _key_lock:
        _key_cooldowns[key_index] = time.time() + cooldown_seconds
    logger.warning(f"Key #{key_index} 配額耗盡，冷卻 {cooldown_seconds} 秒")


def _call_gemini(image: Image.Image, prompt: str, system_instruction: str = "") -> str:
    """
    呼叫 Gemini API，帶多 Key 輪替和指數退避重試

    Args:
        image: PIL Image 物件
        prompt: 使用者 prompt
        system_instruction: 系統指令（限定角色與知識範圍）

    Returns:
        AI 回應文字

    Raises:
        Exception: 所有重試失敗後拋出
    """
    last_error = None

    for attempt in range(config.RETRY_MAX_ATTEMPTS):
        key_idx, api_key = _get_next_key()

        if not api_key:
            break  # 無 Gemini key，跳到備援

        try:
            client = genai.Client(api_key=api_key)

            # 組合 generate_content 參數
            gen_config = None
            if system_instruction:
                gen_config = {
                    "system_instruction": system_instruction
                }

            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[image, prompt],
                config=gen_config
            )

            logger.info(f"Gemini 呼叫成功 (Key #{key_idx}, attempt {attempt + 1})")
            return response.text

        except Exception as e:
            error_str = str(e)
            last_error = e

            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                # 配額耗盡 → 標記 key 冷卻，嘗試下一個
                _mark_key_exhausted(key_idx, cooldown_seconds=60.0)
                logger.warning(
                    f"Gemini Key #{key_idx} 配額耗盡 "
                    f"(attempt {attempt + 1}/{config.RETRY_MAX_ATTEMPTS})"
                )

                # 指數退避等待
                delay = config.RETRY_BASE_DELAY * (2 ** attempt)
                logger.info(f"等待 {delay} 秒後重試...")
                time.sleep(delay)
                continue

            elif "400" in error_str or "INVALID" in error_str:
                # 無效請求，不重試
                logger.error(f"Gemini 無效請求: {e}")
                raise

            else:
                # 其他錯誤，重試
                logger.error(f"Gemini 未知錯誤: {e}")
                delay = config.RETRY_BASE_DELAY * (2 ** attempt)
                time.sleep(delay)
                continue

    # 所有 Gemini key 重試失敗，嘗試 OpenRouter 備援
    logger.warning("所有 Gemini Key 重試失敗，嘗試 OpenRouter 備援...")
    return _call_openrouter_fallback(image, prompt, system_instruction, last_error)


def _image_to_base64(image: Image.Image) -> str:
    """將 PIL Image 轉為 base64 字串"""
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=config.IMAGE_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _call_openrouter_fallback(
    image: Image.Image,
    prompt: str,
    system_instruction: str = "",
    last_gemini_error: Exception = None
) -> str:
    """
    OpenRouter 備援：使用 OpenAI 相容 API 格式

    Args:
        image: PIL Image 物件
        prompt: 使用者 prompt
        system_instruction: 系統指令
        last_gemini_error: 最後一個 Gemini 錯誤（用於日誌）

    Returns:
        AI 回應文字

    Raises:
        Exception: OpenRouter 也失敗時拋出
    """
    if not config.OPENROUTER_API_KEY:
        raise Exception(
            f"所有 Gemini Key 配額耗盡，且未設定 OPENROUTER_API_KEY 備援。"
            f"最後 Gemini 錯誤: {last_gemini_error}"
        )

    logger.info(f"使用 OpenRouter 備援模型: {config.OPENROUTER_MODEL}")

    # 轉換圖片為 base64
    img_b64 = _image_to_base64(image)

    # 組合 messages
    messages = []

    if system_instruction:
        messages.append({
            "role": "system",
            "content": system_instruction
        })

    messages.append({
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{img_b64}"
                }
            },
            {
                "type": "text",
                "text": prompt
            }
        ]
    })

    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://line-art-reviewer.onrender.com",
        "X-Title": "LINE Art Reviewer"
    }

    payload = {
        "model": config.OPENROUTER_MODEL,
        "messages": messages,
        "max_tokens": 1500
    }

    resp = requests.post(
        f"{config.OPENROUTER_BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=60
    )

    if resp.status_code != 200:
        raise Exception(
            f"OpenRouter 備援也失敗 (HTTP {resp.status_code}): {resp.text[:200]}"
        )

    data = resp.json()
    result = data["choices"][0]["message"]["content"]
    logger.info(f"OpenRouter 備援回應成功 ({len(result)} 字元)")
    return result


def analyze_with_vision(
    image: Image.Image,
    prompt: str,
    system_instruction: str = ""
) -> str:
    """
    統一的 AI 圖片分析入口

    自動處理：
    1. Gemini 多 Key 輪替
    2. 429 指數退避重試
    3. OpenRouter 備援

    Args:
        image: PIL Image 物件（已壓縮）
        prompt: 分析 prompt
        system_instruction: 系統指令（限定角色）

    Returns:
        AI 分析結果文字
    """
    return _call_gemini(image, prompt, system_instruction)


def get_status() -> dict:
    """取得 AI 客戶端狀態（用於 health check）"""
    now = time.time()
    keys_status = []
    for i, key in enumerate(config.GOOGLE_API_KEYS):
        cooldown = _key_cooldowns.get(i, 0)
        keys_status.append({
            "index": i,
            "key_prefix": key[:8] + "...",
            "available": now >= cooldown,
            "cooldown_remaining": max(0, cooldown - now)
        })

    return {
        "gemini_keys_count": len(config.GOOGLE_API_KEYS),
        "gemini_keys": keys_status,
        "openrouter_configured": bool(config.OPENROUTER_API_KEY),
        "openrouter_model": config.OPENROUTER_MODEL if config.OPENROUTER_API_KEY else None,
        "model": config.GEMINI_MODEL,
        "retry_max_attempts": config.RETRY_MAX_ATTEMPTS
    }
