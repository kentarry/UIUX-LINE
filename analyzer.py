"""
LINE 美術圖審查工具 — AI 分析引擎
使用多層 AI 策略進行圖片 UX/UI 分析
"""
import config
import ai_client
from PIL import Image
from pathlib import Path
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)

# ── 快取 Skill Prompt 與知識庫（啟動時載入一次，省 token）──
_skill_prompt_cache = None
_knowledge_cache = None
_system_instruction_cache = None


def _load_skill_prompt() -> str:
    """載入並快取 UX 審查 Skill Prompt"""
    global _skill_prompt_cache
    if _skill_prompt_cache is None:
        _skill_prompt_cache = config.UX_REVIEW_SKILL.read_text(encoding="utf-8")
        logger.info(f"已載入 Skill Prompt ({len(_skill_prompt_cache)} 字元)")
    return _skill_prompt_cache


def _load_knowledge() -> str:
    """載入並快取設計規範知識庫（精煉版，只載一個檔案）"""
    global _knowledge_cache
    if _knowledge_cache is None:
        parts = []

        # 核心設計規範（必要 — 已整合所有規則）
        if config.DESIGN_RULES_FILE.exists():
            parts.append(config.DESIGN_RULES_FILE.read_text(encoding="utf-8"))
            logger.info("已載入核心設計規範")

        # 專案特定規範（選用 — 只有非空殼時才載入）
        if config.PROJECT_SPECIFIC_FILE.exists():
            content = config.PROJECT_SPECIFIC_FILE.read_text(encoding="utf-8")
            # 跳過空殼模板（檢查是否有實際填入的內容）
            if "______" not in content and len(content.strip()) > 200:
                parts.append(content)
                logger.info("已載入專案特定規範")
            else:
                logger.info("專案特定規範為空殼模板，跳過")

        _knowledge_cache = "\n\n".join(parts)
        logger.info(f"知識庫總計 {len(_knowledge_cache)} 字元")
    return _knowledge_cache


def _build_system_instruction() -> str:
    """
    建構 system instruction，限定 AI 角色與知識邊界

    精煉版：只給必要約束，不重複 AI 本來就知道的通用知識
    """
    global _system_instruction_cache
    if _system_instruction_cache is None:
        knowledge = _load_knowledge()

        _system_instruction_cache = f"""你是遊戲 UX/UI 設計審查專家。

## 審查依據
根據以下規範審查，每個問題必須引用具體條目：
{knowledge}

## 約束
- 沒違規就肯定，不硬找問題
- 繁體中文，300 字以內
"""
        logger.info(
            f"已建構 system instruction "
            f"({len(_system_instruction_cache)} 字元)"
        )
    return _system_instruction_cache


def reload_cache():
    """強制重新載入 Skill Prompt 與知識庫（更新後使用）"""
    global _skill_prompt_cache, _knowledge_cache, _system_instruction_cache
    _skill_prompt_cache = None
    _knowledge_cache = None
    _system_instruction_cache = None
    _load_skill_prompt()
    _load_knowledge()
    _build_system_instruction()
    logger.info("已重新載入所有快取")


def compress_image(image_path: Path) -> Image.Image:
    """
    壓縮圖片至指定大小以減少 vision token 消耗

    Args:
        image_path: 原始圖片路徑

    Returns:
        壓縮後的 PIL Image 物件
    """
    img = Image.open(image_path)

    # 轉為 RGB（去除 alpha channel）
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # 等比縮放至 max_size
    max_size = config.IMAGE_MAX_SIZE
    if max(img.size) > max_size:
        ratio = max_size / max(img.size)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)
        logger.info(f"圖片已壓縮: {img.size}")

    return img


def analyze_image(image_path: Path, context: str = "") -> dict:
    """
    使用多層 AI 策略分析圖片的 UX/UI 設計

    自動處理：
    - Gemini 多 Key 輪替
    - 429 指數退避重試
    - OpenRouter 備援

    Args:
        image_path: 圖片路徑
        context: 額外上下文（如美術的留言）

    Returns:
        dict: {
            "analysis": 分析結果文字,
            "model": 使用的模型,
            "timestamp": 分析時間,
            "image_path": 圖片路徑
        }
    """
    # 1. 載入本地資源（快取）
    skill_prompt = _load_skill_prompt()
    system_instruction = _build_system_instruction()

    # 2. 壓縮圖片
    img = compress_image(image_path)

    # 3. 組合使用者 Prompt（精簡版 — 知識庫在 system_instruction）
    user_prompt = skill_prompt

    if context:
        user_prompt += f"\n---\n## 美術補充說明\n{context}\n"

    user_prompt += "\n請根據系統規範分析這張圖片。"

    # 4. 呼叫 AI（透過 ai_client，自動處理輪替、重試與備援）
    analysis_text = ai_client.analyze_with_vision(
        image=img,
        prompt=user_prompt,
        system_instruction=system_instruction
    )

    result = {
        "analysis": analysis_text,
        "model": config.GEMINI_MODEL,
        "timestamp": datetime.now().isoformat(),
        "image_path": str(image_path)
    }

    # 5. 儲存分析日誌
    _save_log(result)

    return result


def _save_log(result: dict):
    """儲存分析日誌"""
    timestamp = datetime.now().strftime("%Y%m%d")
    log_file = config.LOGS_DIR / f"analysis_{timestamp}.jsonl"

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")

    logger.info(f"分析日誌已儲存: {log_file}")


def format_for_line(analysis: str) -> str:
    """
    將分析結果格式化為 LINE 友善的純文字格式
    去除 Markdown 語法，保留結構

    Args:
        analysis: 原始分析文字

    Returns:
        LINE 友善格式的文字
    """
    # 移除 markdown code block 標記
    text = analysis.replace("```", "")

    # 確保不超過 LINE 限制
    if len(text) > 4800:
        text = text[:4750] + "\n\n⚠️ 分析過長，已截斷。完整報告請查看日誌。"

    return text.strip()
