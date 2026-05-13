"""
LINE 美術圖審查工具 — CLI 入口
提供手動分析、遊戲管理、知識庫同步、伺服器啟動等功能
"""
import sys
import os

# Windows console UTF-8 支援
if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import config
import analyzer
import session_manager
from pathlib import Path
from datetime import datetime
import json


def cmd_analyze(args):
    """分析圖片"""
    image_path = Path(args.image)

    if not image_path.exists():
        print(f"❌ 圖片不存在: {image_path}")
        sys.exit(1)

    # 遊戲選擇
    game_name = args.game
    if not game_name:
        supported = session_manager.get_supported_games_text()
        print(f"❌ 請用 --game 指定遊戲名稱")
        print(f"   支援的遊戲：{supported}")
        sys.exit(1)

    if game_name not in session_manager.SUPPORTED_GAMES:
        supported = session_manager.get_supported_games_text()
        print(f"❌ 不支援的遊戲: {game_name}")
        print(f"   支援的遊戲：{supported}")
        sys.exit(1)

    print(f"📸 正在分析: {image_path.name}")
    print(f"🎮 遊戲: {game_name}")
    print(f"🤖 模型: {config.GEMINI_MODEL}")
    print(f"🔑 Gemini Keys: {len(config.GOOGLE_API_KEYS)} 個")
    print("─" * 50)

    context = args.context or ""
    result = analyzer.analyze_image(image_path, game_name=game_name, context=context)

    formatted = analyzer.format_for_line(result["analysis"], game_name=game_name)
    print(formatted)
    print("─" * 50)
    print(f"📝 日誌已儲存至: {config.LOGS_DIR}")

    # 如果需要回覆到 LINE
    if args.push_to:
        import line_client
        print(f"\n📤 推送到 LINE: {args.push_to}")
        success = line_client.push_text(args.push_to, formatted)
        if success:
            print("✅ 推送成功")
        else:
            print("❌ 推送失敗")


def cmd_games(args):
    """列出支援的遊戲與知識庫狀態"""
    print("🎮 支援的遊戲")
    print("─" * 50)

    for game_name, game_info in session_manager.SUPPORTED_GAMES.items():
        kb_file = config.KNOWLEDGE_DIR / game_info["knowledge_file"]
        aliases = "、".join(game_info["aliases"])

        if kb_file.exists():
            content = kb_file.read_text(encoding="utf-8")
            size = kb_file.stat().st_size
            if "待填入" in content:
                status = f"⚪ 待填入 ({size:,} bytes)"
            else:
                status = f"✅ 已配置 ({size:,} bytes)"
        else:
            status = "❌ 檔案不存在"

        print(f"\n  📦 {game_name}")
        print(f"     狀態: {status}")
        print(f"     知識庫: {kb_file.name}")
        print(f"     別名: {aliases}")

    # 通用知識庫
    print(f"\n{'─' * 50}")
    print("📚 通用知識庫")
    dr = config.DESIGN_RULES_FILE
    if dr.exists():
        size = dr.stat().st_size
        print(f"  ✅ {dr.name} ({size:,} bytes)")
    else:
        print(f"  ❌ {dr.name} (不存在)")

    # NotebookLM 連結
    print(f"\n{'─' * 50}")
    print("🔗 NotebookLM 筆記本")
    for nb_key, nb_info in config.NOTEBOOKLM_NOTEBOOKS.items():
        print(f"  📓 {nb_info['name']}: {nb_info['description']}")
        print(f"     URL: {nb_info['url']}")
        print(f"     → {nb_info['target_file']}")


def cmd_history(args):
    """查看分析歷史"""
    log_files = sorted(config.LOGS_DIR.glob("analysis_*.jsonl"), reverse=True)

    if not log_files:
        print("📭 尚無分析紀錄")
        return

    # 篩選遊戲
    game_filter = args.game if hasattr(args, "game") else None

    limit = args.limit or 10
    count = 0

    for log_file in log_files:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                if count >= limit:
                    return
                entry = json.loads(line)

                # 遊戲篩選
                entry_game = entry.get("game", "未知")
                if game_filter and game_filter != entry_game:
                    continue

                ts = entry.get("timestamp", "?")
                img = Path(entry.get("image_path", "?")).name
                analysis_preview = entry.get("analysis", "")[:80]
                print(f"[{ts}] 🎮 {entry_game} | {img}")
                print(f"  {analysis_preview}...")
                print()
                count += 1


def cmd_serve(args):
    """啟動 webhook 伺服器"""
    # 驗證設定
    missing = config.validate()
    if missing:
        print("❌ 缺少必要設定:")
        for m in missing:
            print(f"  - {m}")
        print("\n請設定 .env 檔案（參考 .env.example）")
        sys.exit(1)

    supported = session_manager.get_supported_games_text()
    print("🚀 啟動 LINE 美術圖審查伺服器")
    print(f"   Port: {config.PORT}")
    print(f"   Model: {config.GEMINI_MODEL}")
    print(f"   Gemini Keys: {len(config.GOOGLE_API_KEYS)} 個")
    print(f"   OpenRouter 備援: {'✅ 已設定' if config.OPENROUTER_API_KEY else '❌ 未設定'}")
    print(f"   支援遊戲: {supported}")
    print(f"   Webhook: http://localhost:{config.PORT}/callback")
    print()

    import server
    server.run(debug=args.debug)


def cmd_test_line(args):
    """測試 LINE 連線"""
    missing = config.validate()
    line_missing = [m for m in missing if "LINE" in m]
    if line_missing:
        print("❌ LINE 設定不完整:")
        for m in line_missing:
            print(f"  - {m}")
        sys.exit(1)

    import line_client

    # 測試 Bot 資訊
    import requests
    url = "https://api.line.me/v2/bot/info"
    headers = {
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    resp = requests.get(url, headers=headers, timeout=10)

    if resp.status_code == 200:
        info = resp.json()
        print("✅ LINE Bot 連線正常")
        print(f"   Bot 名稱: {info.get('displayName', '?')}")
        print(f"   Bot ID: {info.get('userId', '?')}")
    else:
        print(f"❌ LINE Bot 連線失敗 (HTTP {resp.status_code})")
        print(f"   {resp.text}")


def cmd_test_gemini(args):
    """測試 Gemini API 連線（測試所有 Key）"""
    if not config.GOOGLE_API_KEYS:
        print("❌ GOOGLE_API_KEY(S) 未設定")
        sys.exit(1)

    from google import genai

    print(f"🔑 共 {len(config.GOOGLE_API_KEYS)} 個 Gemini Key")
    print()

    for i, key in enumerate(config.GOOGLE_API_KEYS):
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents="回覆「連線成功」三個字"
            )
            print(f"  ✅ Key #{i} ({key[:8]}...): {response.text.strip()}")
        except Exception as e:
            error_str = str(e)
            if "429" in error_str:
                print(f"  ⚠️ Key #{i} ({key[:8]}...): 配額已滿 (429)")
            else:
                print(f"  ❌ Key #{i} ({key[:8]}...): {error_str[:80]}")

    # 測試 OpenRouter 備援
    print()
    if config.OPENROUTER_API_KEY:
        import requests
        try:
            headers = {
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": config.OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": "回覆「連線成功」三個字"}],
                "max_tokens": 50
            }
            resp = requests.post(
                f"{config.OPENROUTER_BASE_URL}/chat/completions",
                headers=headers, json=payload, timeout=30
            )
            if resp.status_code == 200:
                result = resp.json()["choices"][0]["message"]["content"]
                print(f"  ✅ OpenRouter ({config.OPENROUTER_MODEL}): {result.strip()}")
            else:
                print(f"  ❌ OpenRouter: HTTP {resp.status_code}")
        except Exception as e:
            print(f"  ❌ OpenRouter: {e}")
    else:
        print("  ℹ️ OpenRouter 備援: 未設定（可選）")


def cmd_update_knowledge(args):
    """更新知識庫快取"""
    print("🔄 重新載入知識庫...")
    analyzer.reload_cache()
    print("✅ 知識庫快取已清除（下次分析時重新載入）")
    print(f"   Skill: {config.UX_REVIEW_SKILL.name}")

    # 通用知識庫
    dr = config.DESIGN_RULES_FILE
    if dr.exists():
        size = dr.stat().st_size
        print(f"   基礎規範: {dr.name} ({size:,} bytes)")
    else:
        print(f"   ❌ 基礎規範: {dr.name} (不存在)")

    # 顯示遊戲專屬知識庫狀態
    print()
    print("🎮 遊戲知識庫:")
    for game_name, game_info in session_manager.SUPPORTED_GAMES.items():
        kb_file = config.KNOWLEDGE_DIR / game_info["knowledge_file"]
        if kb_file.exists():
            content = kb_file.read_text(encoding="utf-8")
            size = kb_file.stat().st_size
            if "待填入" in content:
                print(f"   ⚪ {game_name}: {kb_file.name} (待填入 NotebookLM 內容)")
            else:
                print(f"   ✅ {game_name}: {kb_file.name} ({size:,} bytes)")
        else:
            print(f"   ❌ {game_name}: {kb_file.name} (檔案不存在)")

    # NotebookLM
    print()
    print("🔗 NotebookLM 筆記本:")
    for nb_key, nb_info in config.NOTEBOOKLM_NOTEBOOKS.items():
        print(f"   📓 {nb_info['name']} → {nb_info['target_file']}")


def cmd_sync(args):
    """
    從 NotebookLM 同步知識庫（調用 sync_knowledge.py）
    """
    import asyncio

    # 動態 import sync_knowledge（它有自己的 argparse）
    try:
        import sync_knowledge
    except ImportError:
        print("❌ sync_knowledge.py 不存在")
        sys.exit(1)

    if args.login:
        print("🔐 開啟瀏覽器登入 Google...")
        asyncio.run(sync_knowledge.login_google(headed=True))
        return

    # 決定同步目標
    notebook_url = args.notebook_url or config.NOTEBOOKLM_UIUX_URL

    if not notebook_url:
        print("❌ 請提供 NotebookLM URL")
        print("   用法: python cli.py sync --notebook-url <URL>")
        print(f"   或設定 .env 中的 NOTEBOOKLM_UIUX_URL")
        sys.exit(1)

    print()
    print("═" * 50)
    print("  NotebookLM → 知識庫同步")
    print("═" * 50)
    print(f"  URL: {notebook_url}")
    print()

    success = asyncio.run(sync_knowledge.extract_from_notebooklm(
        notebook_url=notebook_url,
        extract_keys=args.only,
        headed=args.headed,
    ))

    if success:
        print("\n✅ 知識庫同步完成！")
        # 自動重載快取
        analyzer.reload_cache()
        print("✅ 快取已重新載入")
    else:
        print("\n❌ 同步失敗，請檢查日誌")
        sys.exit(1)


def cmd_check(args):
    """檢查環境設定"""
    print("🔍 環境檢查")
    print("─" * 50)

    # Python
    print(f"  Python: {sys.version.split()[0]}")

    # 目錄
    dirs = {
        "Skills": config.SKILLS_DIR,
        "Knowledge": config.KNOWLEDGE_DIR,
        "Images": config.IMAGES_DIR,
        "Logs": config.LOGS_DIR,
    }
    for name, path in dirs.items():
        status = "✅" if path.exists() else "❌"
        print(f"  {status} {name}: {path}")

    # 檔案
    print()
    files = {
        "Skill Prompt": config.UX_REVIEW_SKILL,
        "核心規範 (UIUX)": config.DESIGN_RULES_FILE,
    }
    for name, path in files.items():
        if path.exists():
            size = path.stat().st_size
            print(f"  ✅ {name}: {path.name} ({size:,} bytes)")
        else:
            print(f"  ❌ {name}: {path.name} (不存在)")

    # 遊戲知識庫
    print()
    print("  🎮 遊戲知識庫:")
    for game_name, game_info in session_manager.SUPPORTED_GAMES.items():
        kb_file = config.KNOWLEDGE_DIR / game_info["knowledge_file"]
        if kb_file.exists():
            content = kb_file.read_text(encoding="utf-8")
            size = kb_file.stat().st_size
            status = "⚪ 待填入" if "待填入" in content else f"✅ {size:,}b"
            print(f"     {status} {game_name}: {kb_file.name}")
        else:
            print(f"     ❌ {game_name}: {kb_file.name}")

    # API Keys
    print()
    print(f"  🔑 Gemini API Keys: {len(config.GOOGLE_API_KEYS)} 個")
    for i, key in enumerate(config.GOOGLE_API_KEYS):
        print(f"     Key #{i}: {key[:8]}...")

    print(f"  {'✅' if config.LINE_CHANNEL_ACCESS_TOKEN else '❌ 未設定'} LINE_CHANNEL_ACCESS_TOKEN")
    print(f"  {'✅' if config.LINE_CHANNEL_SECRET else '❌ 未設定'} LINE_CHANNEL_SECRET")
    print(f"  {'✅' if config.OPENROUTER_API_KEY else '⚪ (選用)'} OPENROUTER_API_KEY")

    # NotebookLM
    print()
    print("  🔗 NotebookLM:")
    for nb_key, nb_info in config.NOTEBOOKLM_NOTEBOOKS.items():
        print(f"     📓 {nb_info['name']}: {nb_info['url'][:60]}...")

    # Dependencies
    print()
    deps = ["flask", "google.genai", "PIL", "dotenv", "waitress", "requests"]
    for dep in deps:
        try:
            __import__(dep.replace(".", "_") if "." not in dep else dep.split(".")[0])
            print(f"  ✅ {dep}")
        except ImportError:
            print(f"  ❌ {dep} (未安裝)")

    # 總結
    missing = config.validate()
    print()
    if missing:
        print(f"⚠️ 有 {len(missing)} 項需要處理")
    else:
        print("✅ 環境就緒！")


def cmd_status(args):
    """顯示系統即時狀態（含待處理圖片佇列）"""
    print("📊 系統狀態")
    print("─" * 50)
    print(f"  🎮 活躍 Session: {session_manager.get_active_sessions_count()}")
    print(f"  📸 待處理圖片: {session_manager.get_pending_images_count()}")
    print(f"  🤖 模型: {config.GEMINI_MODEL}")
    print(f"  🔑 Gemini Keys: {len(config.GOOGLE_API_KEYS)} 個")
    print(f"  🔄 OpenRouter: {'✅' if config.OPENROUTER_API_KEY else '❌'}")
    print()
    print("💡 Token 節省機制：")
    print("   • 使用者貼圖 → 只下載暫存（0 token）")
    print("   • 確認遊戲後 → 才載入知識庫 + 呼叫 AI")
    print("   • Skill prompt 已精簡（~40% 減少）")


def cmd_cleanup(args):
    """清理暫存圖片"""
    count = 0
    for f in config.IMAGES_DIR.glob("*.jpg"):
        f.unlink()
        count += 1
    print(f"🗑️ 已清理 {count} 張暫存圖片")


def main():
    parser = argparse.ArgumentParser(
        description="LINE 美術圖審查工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用範例:
  python cli.py check                                # 檢查環境
  python cli.py games                                # 列出支援遊戲
  python cli.py serve                                # 啟動伺服器
  python cli.py analyze img.png -g 明星3缺1          # 分析圖片
  python cli.py history                              # 查看分析歷史
  python cli.py history -g 明星3缺1                  # 篩選特定遊戲歷史
  python cli.py test-line                            # 測試 LINE 連線
  python cli.py test-gemini                          # 測試 Gemini Key
  python cli.py update-knowledge                     # 重載知識庫快取
  python cli.py sync --login                         # 登入 Google
  python cli.py status                               # 查看系統狀態
  python cli.py sync --headed                        # 從 NotebookLM 同步
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="分析圖片")
    p_analyze.add_argument("image", help="圖片路徑")
    p_analyze.add_argument("--game", "-g", required=True,
                           help=f"遊戲名稱（{session_manager.get_supported_games_text()}）")
    p_analyze.add_argument("--context", "-c", help="額外說明文字")
    p_analyze.add_argument("--push-to", help="分析後推送到指定 LINE 使用者 ID")
    p_analyze.set_defaults(func=cmd_analyze)

    # games
    p_games = subparsers.add_parser("games", help="列出支援的遊戲與知識庫狀態")
    p_games.set_defaults(func=cmd_games)

    # history
    p_history = subparsers.add_parser("history", help="查看分析歷史")
    p_history.add_argument("--limit", "-n", type=int, default=10, help="顯示筆數")
    p_history.add_argument("--game", "-g", help="篩選特定遊戲")
    p_history.set_defaults(func=cmd_history)

    # serve
    p_serve = subparsers.add_parser("serve", help="啟動 webhook 伺服器")
    p_serve.add_argument("--debug", action="store_true", help="除錯模式")
    p_serve.set_defaults(func=cmd_serve)

    # test-line
    p_test_line = subparsers.add_parser("test-line", help="測試 LINE 連線")
    p_test_line.set_defaults(func=cmd_test_line)

    # test-gemini
    p_test_gemini = subparsers.add_parser("test-gemini", help="測試所有 Gemini Key + OpenRouter")
    p_test_gemini.set_defaults(func=cmd_test_gemini)

    # update-knowledge
    p_update = subparsers.add_parser("update-knowledge", help="重載知識庫快取")
    p_update.set_defaults(func=cmd_update_knowledge)

    # sync
    p_sync = subparsers.add_parser("sync", help="從 NotebookLM 同步知識庫")
    p_sync.add_argument("--login", action="store_true",
                        help="開啟瀏覽器登入 Google（首次使用或 Session 過期時）")
    p_sync.add_argument("--headed", action="store_true",
                        help="顯示瀏覽器（除錯用）")
    p_sync.add_argument("--notebook-url",
                        help="NotebookLM 筆記本 URL（預設使用 .env 設定）")
    p_sync.add_argument("--only", nargs="+",
                        help="只同步指定的知識類型")
    p_sync.set_defaults(func=cmd_sync)

    # check
    p_check = subparsers.add_parser("check", help="檢查環境設定")
    p_check.set_defaults(func=cmd_check)

    # status
    p_status = subparsers.add_parser("status", help="顯示系統即時狀態")
    p_status.set_defaults(func=cmd_status)

    # cleanup
    p_cleanup = subparsers.add_parser("cleanup", help="清理暫存圖片")
    p_cleanup.set_defaults(func=cmd_cleanup)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
