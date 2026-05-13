"""
LINE 美術圖審查工具 — Webhook 伺服器
支援使用者選擇遊戲 → 傳圖片 → 依據對應遊戲 NotebookLM 知識庫分析 → 回覆
"""
import config
import line_client
import analyzer
import ai_client
import session_manager
import hashlib
import hmac
import base64
import logging
import threading
from flask import Flask, request, abort
from datetime import datetime, timedelta

# ── 日誌設定 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            config.LOGS_DIR / "server.log",
            encoding="utf-8"
        )
    ]
)
logger = logging.getLogger("server")

app = Flask(__name__)


def verify_signature(body: bytes, signature: str) -> bool:
    """驗證 LINE webhook 請求簽名"""
    hash_val = hmac.new(
        config.LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    expected = base64.b64encode(hash_val).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def handle_text_message(text: str, reply_token: str, user_id: str, source_type: str):
    """
    處理文字訊息 — 辨識遊戲選擇

    使用者傳送包含遊戲名稱的文字（如「這是明星3缺1」），
    系統會記住使用者選擇的遊戲。
    如果有暫存的待處理圖片，會自動觸發分析（省去重新貼圖）。
    """
    # 嘗試辨識遊戲名稱
    game_name = session_manager.match_game(text)

    if game_name:
        # 設定使用者的遊戲選擇
        session_manager.set_game(user_id, game_name)
        logger.info(f"使用者 {user_id[:8]}... 選擇遊戲: {game_name}")

        # 檢查是否有暫存的待處理圖片
        pending = session_manager.get_pending_image(user_id)

        if pending:
            # 有待處理圖片 → 自動觸發分析（省 token：只在確認遊戲後才呼叫 AI）
            logger.info(
                f"使用者 {user_id[:8]}... 有待處理圖片，自動觸發分析: "
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
                args=(pending, game_name, user_id, source_type),
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
        # 未辨識到遊戲 — 顯示支援清單
        current_game = session_manager.get_game(user_id)
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


def process_pending_image(pending: dict, game_name: str, user_id: str, source_type: str):
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
            line_client.push_text(user_id, "⚠️ 暫存圖片已過期，請重新傳送截圖。")
            return

        logger.info(f"開始分析暫存圖片: {image_path.name}, game={game_name}")

        result = analyzer.analyze_image(image_path, game_name=game_name)
        formatted = analyzer.format_for_line(result["analysis"], game_name=game_name)

        if source_type == "user":
            line_client.push_text(user_id, formatted)
        logger.info(f"暫存圖片分析完成: user={user_id[:8]}..., game={game_name}")

    except Exception as e:
        logger.error(f"暫存圖片分析失敗: {e}", exc_info=True)
        error_msg = f"⚠️ 圖片分析失敗，請重新傳送截圖。\n錯誤: {str(e)[:100]}"
        try:
            if source_type == "user":
                line_client.push_text(user_id, error_msg)
        except Exception:
            pass


def process_image_async(message_id: str, reply_token: str, user_id: str, source_type: str):
    """
    非同步處理圖片（避免 webhook timeout）

    Token 節省策略：
    - 如果使用者尚未選擇遊戲 → 只下載暫存圖片，不呼叫 AI（0 token）
    - 等使用者確認遊戲後才觸發 AI 分析
    """
    try:
        # 1. 檢查使用者是否已選擇遊戲
        game_name = session_manager.get_game(user_id)

        if not game_name:
            # ── 尚未選遊戲：只下載暫存，不呼叫 AI（節省 token）──
            logger.info(f"使用者 {user_id[:8]}... 尚未選遊戲，暫存圖片: {message_id}")

            image_path = line_client.download_image(message_id)
            session_manager.set_pending_image(user_id, image_path, message_id)

            supported = session_manager.get_supported_games_text()
            prompt_msg = (
                f"📸 圖片已收到！\n\n"
                f"🎮 請告訴我這是哪款遊戲，確認後立即分析：\n\n"
                f"支援的遊戲：{supported}\n\n"
                f"💡 直接輸入遊戲名稱即可（例如：明星3缺1）"
            )
            if not line_client.reply_text(reply_token, prompt_msg):
                if source_type == "user":
                    line_client.push_text(user_id, prompt_msg)
            return

        logger.info(f"開始處理圖片: message_id={message_id}, game={game_name}")

        # 2. 下載圖片
        image_path = line_client.download_image(message_id)
        logger.info(f"圖片已下載: {image_path}")

        # 3. AI 分析（帶入遊戲知識庫）
        result = analyzer.analyze_image(image_path, game_name=game_name)
        logger.info(f"分析完成: {len(result['analysis'])} 字元")

        # 4. 格式化並回覆
        formatted = analyzer.format_for_line(
            result["analysis"],
            game_name=game_name
        )

        # 嘗試用 reply_token（可能已過期）
        success = line_client.reply_text(reply_token, formatted)

        if not success:
            # reply_token 過期，改用 push（僅限 1 對 1）
            logger.warning("reply_token 已過期，嘗試 push 訊息")
            if source_type == "user":
                line_client.push_text(user_id, formatted)
            else:
                logger.error("群組訊息無法使用 push，reply_token 已過期")

        logger.info(f"回覆完成: user={user_id}, game={game_name}")

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
                if source_type == "user":
                    line_client.push_text(user_id, error_msg)
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

        # ── 處理文字訊息（遊戲選擇）──
        if message_type == "text":
            text = message.get("text", "").strip()
            if text:
                logger.info(
                    f"收到文字: user={user_id}, text={text[:50]}"
                )
                handle_text_message(text, reply_token, user_id, source_type)
            continue

        # ── 處理圖片訊息 ──
        if message_type == "image":
            message_id = message.get("id")

            logger.info(
                f"收到圖片: message_id={message_id}, "
                f"source={source_type}, user={user_id}"
            )

            # 非同步處理（避免 webhook timeout）
            thread = threading.Thread(
                target=process_image_async,
                args=(message_id, reply_token, user_id, source_type),
                daemon=True
            )
            thread.start()

    return "OK", 200


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
    """啟動伺服器"""
    port = port or config.PORT

    # 啟動時清理舊圖片
    cleanup_old_images()

    # 預載入快取
    analyzer.reload_cache()

    logger.info(f"伺服器啟動: http://{host}:{port}")
    logger.info(f"Webhook URL: http://{host}:{port}/callback")
    logger.info(f"Health Check: http://{host}:{port}/health")
    logger.info(f"Gemini Keys: {len(config.GOOGLE_API_KEYS)} 個")
    logger.info(f"OpenRouter 備援: {'已設定' if config.OPENROUTER_API_KEY else '未設定'}")
    logger.info(f"支援遊戲: {session_manager.get_supported_games_text()}")

    if debug:
        app.run(host=host, port=port, debug=True)
    else:
        # 正式環境使用 waitress
        from waitress import serve
        serve(app, host=host, port=port)


if __name__ == "__main__":
    run(debug=True)
