"""
LINE 美術圖審查工具 — AI 分析引擎
支援多遊戲知識庫切換，根據使用者選擇的遊戲載入對應 NotebookLM 知識
輸出 JSON 結構化結果，格式化為 LINE 友善文字
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

    只載入遊戲專屬知識庫，不再重複載入通用設計規範（已整合至 system instruction）

    Args:
        game_name: 遊戲名稱（如 "明星3缺1"）

    Returns:
        知識庫文字內容
    """
    if game_name in _game_knowledge_cache:
        return _game_knowledge_cache[game_name]

    parts = []

    # 遊戲專屬知識庫
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

    精簡版：只包含遊戲脈絡 + 通用高頻退回原因，
    不再重複塞入整份 design_rules.md（節省 ~5000 token/次）

    Args:
        game_name: 遊戲名稱

    Returns:
        完整的 system instruction 文字
    """
    if game_name in _game_system_instruction_cache:
        return _game_system_instruction_cache[game_name]

    knowledge = _load_game_knowledge(game_name)

    instruction = f"""你是「{game_name}」遊戲的 UI/UX 審查專家。

## 遊戲脈絡
{knowledge}

## 審查核心原則
- 只指出真正影響使用體驗的問題，不為了改而改
- 設計接近完美就直接肯定，不硬找問題
- 每個問題必須引用具體規範或原理作為依據
- 繁體中文回覆

## 高頻退回判定標準（僅供參考，非必須全檢）
| 問題 | 判定標準 |
|------|----------|
| 按鈕/點擊區太小 | < 44×44px |
| 文字對比度不足 | < 4.5:1 |
| 間距不一致 | 同類元素間距差 > 2px |
| 視覺層級模糊 | 無明確 CTA 或多個等權重元素 |
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


def _parse_json_response(text: str) -> dict | None:
    """
    從 AI 回應中解析 JSON

    支援多種格式：
    - 純 JSON
    - 包在 ```json ... ``` code block 中
    - 前後有多餘文字

    Returns:
        解析後的 dict，或 None（解析失敗）
    """
    # 移除 markdown code block
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 移除 ```json 和 ```
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    # 嘗試找到 JSON 物件
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start:end])
        except json.JSONDecodeError:
            pass

    return None


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
            "analysis": 分析結果文字（原始 JSON 字串）,
            "parsed": 解析後的 dict（含 observation/suggestion）,
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

    # 3. 組合使用者 Prompt（精簡，減少 token）
    user_prompt = skill_prompt

    if context:
        user_prompt += f"\n---\n## 美術補充說明\n{context}\n"

    user_prompt += f"\n這是「{game_name}」的介面截圖，請分析並以 JSON 格式回覆。"

    # 4. 呼叫 AI（透過 ai_client，自動處理輪替、重試與備援）
    analysis_text = ai_client.analyze_with_vision(
        image=img,
        prompt=user_prompt,
        system_instruction=system_instruction
    )

    # 5. 嘗試解析 JSON
    parsed = _parse_json_response(analysis_text)
    if not parsed:
        logger.warning("AI 回應非 JSON 格式，保留原始文字")
        parsed = {
            "observation": [analysis_text[:500]],
            "suggestion": []
        }

    result = {
        "analysis": analysis_text,
        "parsed": parsed,
        "model": config.GEMINI_MODEL,
        "game": game_name,
        "timestamp": datetime.now().isoformat(),
        "image_path": str(image_path)
    }

    # 6. 儲存分析日誌
    _save_log(result)

    return result


def _save_log(result: dict):
    """儲存分析日誌"""
    timestamp = datetime.now().strftime("%Y%m%d")
    log_file = config.LOGS_DIR / f"analysis_{timestamp}.jsonl"

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")

    logger.info(f"分析日誌已儲存: {log_file}")


def format_for_line(analysis: str, game_name: str = "", parsed: dict = None) -> str:
    """
    將分析結果格式化為 LINE 友善的純文字格式

    優先使用 parsed JSON 結構化輸出，否則 fallback 到原始文字

    Args:
        analysis: 原始分析文字（fallback 用）
        game_name: 遊戲名稱（加在標頭）
        parsed: 解析後的 JSON dict（含 observation/suggestion）

    Returns:
        LINE 友善格式的文字
    """
    lines = []

    # 標頭
    if game_name:
        lines.append(f"🎮 {game_name} — UI/UX 分析")
        lines.append("")

    if parsed and isinstance(parsed, dict):
        # ── 結構化 JSON 輸出 ──
        observations = parsed.get("observation", [])
        suggestions = parsed.get("suggestion", [])

        if observations:
            lines.append("📋 觀察：")
            for i, obs in enumerate(observations, 1):
                lines.append(f"  {i}. {obs}")
            lines.append("")

        if suggestions:
            # 排序保障：修正建議在前，✅ 亮點排到最後
            issues = [s for s in suggestions if "✅ 亮點" not in s]
            highlights = [s for s in suggestions if "✅ 亮點" in s]
            sorted_suggestions = issues + highlights

            lines.append("💡 建議：")
            for i, sug in enumerate(sorted_suggestions, 1):
                lines.append(f"  {i}. {sug}")
            lines.append("")
        elif observations:
            # 有觀察但無建議 → 接近完美
            lines.append("✅ 設計品質優良，無需額外修改。")
            lines.append("")

    else:
        # ── Fallback：原始文字 ──
        text = analysis.replace("```", "")
        lines.append(text)

    result = "\n".join(lines).strip()

    # 確保不超過 LINE 限制
    if len(result) > 4800:
        result = result[:4750] + "\n\n⚠️ 分析過長，已截斷。完整報告請查看日誌。"

    return result
