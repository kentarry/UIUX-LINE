"""
LINE 美術圖審查工具 — AI 分析引擎
支援多遊戲知識庫切換，根據使用者選擇的遊戲載入對應 NotebookLM 知識
"""
import config
import ai_client
import session_manager
from PIL import Image
from pathlib import Path
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)

# ── 快取：按遊戲分別快取知識庫與 system instruction ──
_skill_prompt_cache = None
_game_knowledge_cache: dict[str, str] = {}
_game_system_instruction_cache: dict[str, str] = {}


def _load_skill_prompt() -> str:
    """載入並快取 UX 審查 Skill Prompt"""
    global _skill_prompt_cache
    if _skill_prompt_cache is None:
        _skill_prompt_cache = config.UX_REVIEW_SKILL.read_text(encoding="utf-8")
        logger.info(f"已載入 Skill Prompt ({len(_skill_prompt_cache)} 字元)")
    return _skill_prompt_cache


def _load_game_knowledge(game_name: str) -> str:
    """
    載入指定遊戲的知識庫（從 knowledge/ 目錄讀取對應 MD）

    Args:
        game_name: 遊戲名稱（如 "明星3缺1"）

    Returns:
        知識庫文字內容
    """
    if game_name in _game_knowledge_cache:
        return _game_knowledge_cache[game_name]

    parts = []

    # 1. 通用設計規範（所有遊戲共用）
    if config.DESIGN_RULES_FILE.exists():
        parts.append(config.DESIGN_RULES_FILE.read_text(encoding="utf-8"))
        logger.info("已載入通用設計規範")

    # 2. 遊戲專屬知識庫
    game_info = session_manager.get_game_info(game_name)
    if game_info:
        game_kb_file = config.KNOWLEDGE_DIR / game_info["knowledge_file"]
        if game_kb_file.exists():
            content = game_kb_file.read_text(encoding="utf-8")
            parts.append(content)
            logger.info(f"已載入 {game_name} 專屬知識庫 ({len(content)} 字元)")
        else:
            logger.warning(f"遊戲知識庫檔案不存在: {game_kb_file}")

    knowledge = "\n\n".join(parts)
    _game_knowledge_cache[game_name] = knowledge
    logger.info(f"{game_name} 知識庫總計 {len(knowledge)} 字元")
    return knowledge


def _build_game_system_instruction(game_name: str) -> str:
    """
    建構指定遊戲的 system instruction

    根據使用者選擇的遊戲，載入對應的 NotebookLM 知識庫，
    限定 AI 角色與知識邊界。

    Args:
        game_name: 遊戲名稱

    Returns:
        完整的 system instruction 文字
    """
    if game_name in _game_system_instruction_cache:
        return _game_system_instruction_cache[game_name]

    knowledge = _load_game_knowledge(game_name)

    instruction = f"""你是「{game_name}」遊戲的 UX/UI 設計審查專家。

## 你的身份
你專精於「{game_name}」這款遊戲的介面設計審查，熟悉該遊戲的視覺風格、品牌規範與設計模式。

## 審查依據
根據以下規範審查，每個問題必須引用具體條目：
{knowledge}

## 約束
- 只針對「{game_name}」的設計脈絡進行分析
- 沒違規就肯定，不硬找問題
- 繁體中文，300 字以內
- 不需要評分，直接分析
"""

    _game_system_instruction_cache[game_name] = instruction
    logger.info(
        f"已建構 {game_name} system instruction "
        f"({len(instruction)} 字元)"
    )
    return instruction


def reload_cache():
    """強制重新載入所有快取（知識庫更新後使用）"""
    global _skill_prompt_cache, _game_knowledge_cache, _game_system_instruction_cache
    _skill_prompt_cache = None
    _game_knowledge_cache = {}
    _game_system_instruction_cache = {}
    _load_skill_prompt()
    logger.info("已清除所有快取（下次分析時重新載入對應遊戲知識庫）")


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


def analyze_image(image_path: Path, game_name: str, context: str = "") -> dict:
    """
    使用多層 AI 策略分析圖片的 UX/UI 設計

    根據使用者選擇的遊戲，載入對應知識庫進行分析。

    自動處理：
    - Gemini 多 Key 輪替
    - 429 指數退避重試
    - OpenRouter 備援

    Args:
        image_path: 圖片路徑
        game_name: 遊戲名稱（用於載入對應知識庫）
        context: 額外上下文（如美術的留言）

    Returns:
        dict: {
            "analysis": 分析結果文字,
            "model": 使用的模型,
            "game": 遊戲名稱,
            "timestamp": 分析時間,
            "image_path": 圖片路徑
        }
    """
    # 1. 載入本地資源（快取）
    skill_prompt = _load_skill_prompt()
    system_instruction = _build_game_system_instruction(game_name)

    # 2. 壓縮圖片
    img = compress_image(image_path)

    # 3. 組合使用者 Prompt
    user_prompt = skill_prompt

    if context:
        user_prompt += f"\n---\n## 美術補充說明\n{context}\n"

    user_prompt += f"\n這是「{game_name}」的介面截圖，請根據系統規範分析這張圖片。"

    # 4. 呼叫 AI（透過 ai_client，自動處理輪替、重試與備援）
    analysis_text = ai_client.analyze_with_vision(
        image=img,
        prompt=user_prompt,
        system_instruction=system_instruction
    )

    result = {
        "analysis": analysis_text,
        "model": config.GEMINI_MODEL,
        "game": game_name,
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


def format_for_line(analysis: str, game_name: str = "") -> str:
    """
    將分析結果格式化為 LINE 友善的純文字格式
    去除 Markdown 語法，保留結構

    Args:
        analysis: 原始分析文字
        game_name: 遊戲名稱（加在標頭）

    Returns:
        LINE 友善格式的文字
    """
    # 移除 markdown code block 標記
    text = analysis.replace("```", "")

    # 加上遊戲標頭
    if game_name:
        text = f"🎮 {game_name} — UI 分析\n{'─' * 20}\n\n{text}"

    # 確保不超過 LINE 限制
    if len(text) > 4800:
        text = text[:4750] + "\n\n⚠️ 分析過長，已截斷。完整報告請查看日誌。"

    return text.strip()
