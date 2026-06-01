"""
LINE 美術圖審查工具 — Webhook 伺服器
支援使用者選擇遊戲 → 傳圖片 → 依據對應遊戲 NotebookLM 知識庫分析 → 回覆
"""
import config
import line_client
import analyzer
import ai_client
import session_manager
import sys
import hashlib
import hmac
import base64
import logging
import threading
from flask import Flask, request, abort
from datetime import datetime, timedelta

# ── 日誌設定（容錯：Render 環境可能無法寫入檔案）──
_log_handlers = [logging.StreamHandler()]
try:
    _log_handlers.append(
        logging.FileHandler(
            config.LOGS_DIR / "server.log",
            encoding="utf-8"
        )
    )
except (OSError, PermissionError):
    pass  # Render 環境可能無法寫入，僅用 stdout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_log_handlers
)
logger = logging.getLogger("server")

app = Flask(__name__)

# ── 模組載入時初始化（gunicorn 直接 import server:app 時需要）──
try:
    def _startup_init():
        """伺服器啟動初始化：清理舊圖、預載快取"""
        try:
            # 清理過期圖片
            threshold = datetime.now() - timedelta(hours=config.IMAGE_RETAIN_HOURS)
            count = 0
            for f in config.IMAGES_DIR.glob("*.jpg"):
                if datetime.fromtimestamp(f.stat().st_mtime) < threshold:
                    f.unlink()
                    count += 1
            if count:
                logger.info(f"已清理 {count} 張過期圖片")
        except Exception:
            pass  # 不阻擋啟動

        # 預載入快取
        analyzer.reload_cache()
        logger.info(f"伺服器初始化完成 | model={config.GEMINI_MODEL} | keys={len(config.GOOGLE_API_KEYS)}")

    _startup_init()
except Exception as e:
    logger.warning(f"啟動初始化部分失敗（不影響服務）: {e}")


def verify_signature(body: bytes, signature: str) -> bool:
    """驗證 LINE webhook 請求簽名"""
    hash_val = hmac.new(
        config.LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    expected = base64.b64encode(hash_val).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def handle_text_message(text: str, reply_token: str, session_id: str, source_type: str, base_url: str = ""):
    """
    處理文字訊息 — 辨識遊戲選擇與產圖確認
    """
    clean_text = text.lower().strip()
    pending_prompt, pending_img = session_manager.get_pending_redesign(session_id)

    # 1. 檢查是否要為先前的分析產圖
    if pending_prompt and clean_text in ["是", "要", "好", "yes", "y", "確定"]:
        session_manager.clear_pending_redesign(session_id)
        reply_msg = "📸 正在依據建議產生改進後的設計圖，請稍候..."
        line_client.reply_text(reply_token, reply_msg)
        
        # 啟動背景執行緒進行圖片生成
        thread = threading.Thread(
            target=process_redesign_generation,
            args=(session_id, pending_prompt, pending_img, base_url),
            daemon=True
        )
        thread.start()
        return

    if pending_prompt and clean_text in ["否", "不用", "取消", "no", "n"]:
        session_manager.clear_pending_redesign(session_id)
        reply_msg = "✅ 已取消產圖。您可以繼續傳送下一張截圖進行分析。"
        line_client.reply_text(reply_token, reply_msg)
        return

    # 2. 嘗試辨識遊戲名稱
    game_name = session_manager.match_game(text)

    if game_name:
        # 切換遊戲時，清除先前待確認的產圖
        session_manager.clear_pending_redesign(session_id)
        # 設定使用者的遊戲選擇與對話內容作為上下文
        session_manager.set_game(session_id, game_name, context=text)
        logger.info(f"使用者 {session_id[:8]}... 選擇遊戲: {game_name}")

        # 檢查是否有暫存的待處理圖片
        pending = session_manager.get_pending_image(session_id)

        if pending:
            # 有待處理圖片 → 自動觸發分析（省 token：只在確認遊戲後才呼叫 AI）
            logger.info(
                f"使用者 {session_id[:8]}... 有待處理圖片，自動觸發分析: "
                f"message_id={pending['message_id']}"
            )
            reply_msg = (
                f"✅ 已確認「{game_name}」\n\n"
                f"📸 正在分析剛才的圖片，請稍候..."
            )
            line_client.reply_text(reply_token, reply_msg)

            # 用暫存圖片進行分析（背景執行緒）
            from pathlib import Path
            thread = threading.Thread(
                target=process_pending_image,
                args=(pending, game_name, session_id, source_type, text, base_url),
                daemon=True
            )
            thread.start()
        else:
            # 沒有待處理圖片 → 正常切換遊戲
            reply_msg = (
                f"✅ 已切換至「{game_name}」\n\n"
                f"請傳送要分析的介面截圖，我會依據「{game_name}」的設計規範進行審查。"
            )
            line_client.reply_text(reply_token, reply_msg)

    else:
        # 如果有待確認的產圖，但輸入不是是/否，且未匹配到新遊戲
        if pending_prompt:
            reply_msg = (
                "📌 偵測到您尚未決定是否要產生建議設計圖。\n\n"
                "👉 請回覆「是」開始產生圖片\n"
                "👉 請回覆「否」取消產圖\n\n"
                "（如需切換至其他遊戲，請直接輸入遊戲名稱）"
            )
            line_client.reply_text(reply_token, reply_msg)
            return

        # 未辨識到遊戲 — 顯示支援清單
        current_game, _ = session_manager.get_game(session_id)
        supported = session_manager.get_supported_games_text()

        if current_game:
            reply_msg = (
                f"📌 目前選擇的遊戲：{current_game}\n\n"
                f"直接傳送截圖即可分析。\n"
                f"如需切換遊戲，請輸入遊戲名稱。\n\n"
                f"🎮 支援的遊戲：{supported}"
            )
        else:
            reply_msg = (
                f"🎮 請先告訴我這是哪款遊戲：\n\n"
                f"支援的遊戲：{supported}\n\n"
                f"範例：「這是明星3缺1」"
            )

        line_client.reply_text(reply_token, reply_msg)


def process_redesign_generation(session_id: str, prompt: str, img_path: str, base_url: str):
    """
    背景執行緒：呼召 Imagen 3 產生設計圖並發送
    """
    from pathlib import Path
    try:
        aspect_ratio = analyzer._get_best_aspect_ratio(Path(img_path))
        redesign_path = analyzer.generate_redesign_image(prompt, aspect_ratio)
        if redesign_path:
            root_url = base_url if base_url.endswith("/") else f"{base_url}/"
            img_url = f"{root_url}images/{redesign_path.name}"
            logger.info(f"正在向 {session_id[:8]} 推送產出的建議設計圖: {img_url}")
            line_client.push_image(session_id, img_url, img_url)
        else:
            line_client.push_text(session_id, "⚠️ 建議設計圖生成失敗，請確認 API 金鑰配額與狀態。")
    except Exception as e:
        logger.error(f"產出建議設計圖失敗: {e}", exc_info=True)
        line_client.push_text(session_id, f"⚠️ 建議設計圖產生失敗: {str(e)[:100]}")


def process_pending_image(pending: dict, game_name: str, session_id: str, source_type: str, context: str = "", base_url: str = ""):
    """
    處理暫存的待處理圖片（使用者先貼圖、後選遊戲時觸發）

    此函式不呼叫 AI 下載圖片（已在收圖時下載），
    只進行 AI 分析，大幅節省不必要的 token 消耗。
    """
    from pathlib import Path
    try:
        image_path = Path(pending["image_path"])
        if not image_path.exists():
            logger.error(f"暫存圖片不存在: {image_path}")
            line_client.push_text(session_id, "⚠️ 暫存圖片已過期，請重新傳送截圖。")
            return

        logger.info(f"開始分析暫存圖片: {image_path.name}, game={game_name}")

        result = analyzer.analyze_image(image_path, game_name=game_name, context=context)
        formatted = analyzer.format_for_line(result["analysis"], game_name=game_name, parsed=result.get("parsed"))

        line_client.push_text(session_id, formatted)
        logger.info(f"暫存圖片分析完成: session={session_id[:8]}..., game={game_name}")

        # ── 暫存設計圖 prompt，並詢問使用者是否要產圖 ──
        redesign_prompt = result.get("redesign_prompt", "")
        if redesign_prompt:
            session_manager.set_pending_redesign(session_id, redesign_prompt, str(image_path))
            prompt_confirm_msg = (
                "💡 是否需要利用 AI 產生改進後的 UI 建議設計圖供您對照？\n"
                "👉 請回覆「是」或「否」"
            )
            line_client.push_text(session_id, prompt_confirm_msg)

    except Exception as e:
        logger.error(f"暫存圖片分析失敗: {e}", exc_info=True)
        error_msg = f"⚠️ 圖片分析失敗，請重新傳送截圖。\n錯誤: {str(e)[:100]}"
        try:
            line_client.push_text(session_id, error_msg)
        except Exception:
            pass


def process_image_async(message_id: str, reply_token: str, session_id: str, source_type: str, base_url: str = ""):
    """
    非同步處理圖片（避免 webhook timeout）

    Token 節省策略：
    - 如果使用者尚未選擇遊戲 → 只下載暫存圖片，不呼叫 AI（0 token）
    - 等使用者確認遊戲後才觸發 AI 分析
    """
    try:
        # 1. 檢查使用者是否已選擇遊戲
        game_name, context = session_manager.get_game(session_id)

        if not game_name:
            # ── 尚未選遊戲：只下載暫存，不呼叫 AI（節省 token）──
            logger.info(f"工作階段 {session_id[:8]}... 尚未選遊戲，暫存圖片: {message_id}")

            image_path = line_client.download_image(message_id)
            session_manager.set_pending_image(session_id, image_path, message_id)

            supported = session_manager.get_supported_games_text()
            prompt_msg = (
                f"📸 圖片已收到！\n\n"
                f"🎮 請告訴我這是哪款遊戲，確認後立即分析：\n\n"
                f"支援的遊戲：{supported}\n\n"
                f"💡 直接輸入遊戲名稱即可（例如：明星3缺1）"
            )
            if not line_client.reply_text(reply_token, prompt_msg):
                line_client.push_text(session_id, prompt_msg)
            return

        logger.info(f"開始處理圖片: message_id={message_id}, game={game_name}")

        # 2. 下載圖片
        image_path = line_client.download_image(message_id)
        logger.info(f"圖片已下載: {image_path}")

        # 3. AI 分析（帶入遊戲知識庫）
        result = analyzer.analyze_image(image_path, game_name=game_name, context=context)
        logger.info(f"分析完成: {len(result['analysis'])} 字元")

        # 4. 格式化並回覆
        formatted = analyzer.format_for_line(
            result["analysis"],
            game_name=game_name,
            parsed=result.get("parsed")
        )

        # 嘗試用 reply_token（可能已過期）
        success = line_client.reply_text(reply_token, formatted)

        if not success:
            # reply_token 已過期，改用 push 訊息
            logger.warning("reply_token 已過期，嘗試 push 訊息")
            line_client.push_text(session_id, formatted)

        logger.info(f"回覆完成: session={session_id}, game={game_name}")

        # ── 5. 暫存設計圖 prompt，並詢問使用者是否要產圖 ──
        redesign_prompt = result.get("redesign_prompt", "")
        if redesign_prompt:
            session_manager.set_pending_redesign(session_id, redesign_prompt, str(image_path))
            prompt_confirm_msg = (
                "💡 是否需要利用 AI 產生改進後的 UI 建議設計圖供您對照？\n"
                "👉 請回覆「是」或「否」"
            )
            line_client.push_text(session_id, prompt_confirm_msg)

    except Exception as e:
        logger.error(f"處理圖片失敗: {e}", exc_info=True)

        # 根據錯誤類型給出不同提示
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "配額耗盡" in str(e):
            error_msg = (
                "⚠️ AI 服務暫時忙碌（API 配額已達上限）\n\n"
                "請稍等 1-2 分鐘後重新傳送圖片。\n"
                "如持續發生，請通知管理員。"
            )
        else:
            error_msg = (
                "⚠️ 圖片分析失敗，請稍後再試。\n"
                f"錯誤: {str(e)[:100]}"
            )

        try:
            # 先嘗試 reply，失敗則 push
            if not line_client.reply_text(reply_token, error_msg):
                line_client.push_text(session_id, error_msg)
        except Exception:
            pass


@app.route("/callback", methods=["POST"])
def callback():
    """LINE webhook 回呼端點"""
    # 1. 驗證簽名
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data()

    if not verify_signature(body, signature):
        logger.warning("簽名驗證失敗")
        abort(400)

    # 2. 解析事件
    payload = request.get_json()
    events = payload.get("events", [])

    for event in events:
        event_type = event.get("type")

        if event_type != "message":
            continue

        message = event.get("message", {})
        message_type = message.get("type")
        reply_token = event.get("replyToken")
        source = event.get("source", {})
        source_type = source.get("type", "user")
        user_id = source.get("userId", "")

        # 決定工作階段 ID: 優先使用群組或聊天室 ID，以利團隊協作與群組分析推送；個人聊天則用 userId
        session_id = source.get("groupId") or source.get("roomId") or user_id or ""

        # ── 處理文字訊息（遊戲選擇）──
        if message_type == "text":
            text = message.get("text", "").strip()
            if text:
                logger.info(
                    f"收到文字: session={session_id}, text={text[:50]}"
                )
                base_url = request.url_root
                handle_text_message(text, reply_token, session_id, source_type, base_url)
            continue

        # ── 處理圖片訊息 ──
        if message_type == "image":
            message_id = message.get("id")

            logger.info(
                f"收到圖片: message_id={message_id}, "
                f"source={source_type}, session={session_id}"
            )

            base_url = request.url_root
            # 非同步處理（避免 webhook timeout）
            thread = threading.Thread(
                target=process_image_async,
                args=(message_id, reply_token, session_id, source_type, base_url),
                daemon=True
            )
            thread.start()

    return "OK", 200


@app.route("/images/<path:filename>", methods=["GET"])
def serve_image(filename):
    """提供圖片檔案靜態存取（供 LINE 圖片訊息載入）"""
    from flask import send_from_directory
    return send_from_directory(config.IMAGES_DIR, filename)


@app.route("/health", methods=["GET"])
def health():
    """健康檢查端點（也用於 keep-alive ping）"""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "ai": ai_client.get_status(),
        "active_sessions": session_manager.get_active_sessions_count(),
        "pending_images": session_manager.get_pending_images_count(),
        "supported_games": list(session_manager.SUPPORTED_GAMES.keys()),
    }


def cleanup_old_images():
    """清理過期的暫存圖片"""
    threshold = datetime.now() - timedelta(hours=config.IMAGE_RETAIN_HOURS)
    count = 0
    for f in config.IMAGES_DIR.glob("*.jpg"):
        if datetime.fromtimestamp(f.stat().st_mtime) < threshold:
            f.unlink()
            count += 1
    if count:
        logger.info(f"已清理 {count} 張過期圖片")


def run(host="0.0.0.0", port=None, debug=False):
    """啟動伺服器（初始化已在模組載入時完成）"""
    port = port or config.PORT

    logger.info(f"伺服器啟動: http://{host}:{port}")
    logger.info(f"Webhook URL: http://{host}:{port}/callback")
    logger.info(f"Health Check: http://{host}:{port}/health")
    logger.info(f"Gemini Keys: {len(config.GOOGLE_API_KEYS)} 個")
    logger.info(f"OpenRouter 備援: {'已設定' if config.OPENROUTER_API_KEY else '未設定'}")
    logger.info(f"支援遊戲: {session_manager.get_supported_games_text()}")

    if debug:
        app.run(host=host, port=port, debug=True)
    else:
        # 正式環境：Linux 用 gunicorn，Windows 用 waitress
        if sys.platform == "win32":
            from waitress import serve as waitress_serve
            waitress_serve(app, host=host, port=port)
        else:
            import subprocess
            logger.info(f"以 gunicorn 啟動 (port={port})...")
            subprocess.run([
                "gunicorn",
                "--bind", f"{host}:{port}",
                "--workers", "1",
                "--timeout", "120",
                "server:app"
            ])


if __name__ == "__main__":
    run(debug=True)
