"""
LINE 美術圖審查工具 — CLI 入口
提供手動分析、歷史查看、伺服器啟動等功能
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
from pathlib import Path
from datetime import datetime
import json


def cmd_analyze(args):
    """分析圖片"""
    image_path = Path(args.image)

    if not image_path.exists():
        print(f"❌ 圖片不存在: {image_path}")
        sys.exit(1)

    print(f"📸 正在分析: {image_path.name}")
    print(f"🤖 模型: {config.GEMINI_MODEL}")
    print(f"🔑 Gemini Keys: {len(config.GOOGLE_API_KEYS)} 個")
    print("─" * 50)

    context = args.context or ""
    result = analyzer.analyze_image(image_path, context=context)

    formatted = analyzer.format_for_line(result["analysis"])
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


def cmd_history(args):
    """查看分析歷史"""
    log_files = sorted(config.LOGS_DIR.glob("analysis_*.jsonl"), reverse=True)

    if not log_files:
        print("📭 尚無分析紀錄")
        return

    # 取最近 N 天
    limit = args.limit or 10
    count = 0

    for log_file in log_files:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                if count >= limit:
                    return
                entry = json.loads(line)
                ts = entry.get("timestamp", "?")
                img = Path(entry.get("image_path", "?")).name
                analysis_preview = entry.get("analysis", "")[:80]
                print(f"[{ts}] {img}")
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

    print("🚀 啟動 LINE 美術圖審查伺服器")
    print(f"   Port: {config.PORT}")
    print(f"   Model: {config.GEMINI_MODEL}")
    print(f"   Gemini Keys: {len(config.GOOGLE_API_KEYS)} 個")
    print(f"   OpenRouter 備援: {'✅ 已設定' if config.OPENROUTER_API_KEY else '❌ 未設定'}")
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
    print("✅ 知識庫已更新")
    print(f"   Skill: {config.UX_REVIEW_SKILL}")
    print(f"   基礎規範: {config.DESIGN_RULES_FILE}")

    # 顯示擴充知識庫狀態
    extra_files = [
        ("專案規範", config.PROJECT_SPECIFIC_FILE),
    ]
    for name, path in extra_files:
        if path.exists():
            content = path.read_text(encoding="utf-8")
            if "______" in content:
                print(f"   ⚪ {name}: {path.name} (空殼模板，已跳過)")
            else:
                size = path.stat().st_size
                print(f"   ✅ {name}: {path.name} ({size} bytes)")
        else:
            print(f"   ⚪ {name}: 未建立")


def cmd_check(args):
    """檢查環境設定"""
    print("🔍 環境檢查")
    print("─" * 40)

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
        "核心規範": config.DESIGN_RULES_FILE,
        "專案規範": config.PROJECT_SPECIFIC_FILE,
    }
    for name, path in files.items():
        status = "✅" if path.exists() else "⚪ (選用)"
        print(f"  {status} {name}: {path.name}")

    # API Keys
    print()
    print(f"  🔑 Gemini API Keys: {len(config.GOOGLE_API_KEYS)} 個")
    for i, key in enumerate(config.GOOGLE_API_KEYS):
        print(f"     Key #{i}: {key[:8]}...")

    print(f"  {'✅' if config.LINE_CHANNEL_ACCESS_TOKEN else '❌ 未設定'} LINE_CHANNEL_ACCESS_TOKEN")
    print(f"  {'✅' if config.LINE_CHANNEL_SECRET else '❌ 未設定'} LINE_CHANNEL_SECRET")
    print(f"  {'✅' if config.OPENROUTER_API_KEY else '⚪ (選用)'} OPENROUTER_API_KEY")

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
  python cli.py check                      # 檢查環境
  python cli.py serve                      # 啟動伺服器
  python cli.py analyze path/to/image.png  # 手動分析圖片
  python cli.py history                    # 查看分析歷史
  python cli.py test-line                  # 測試 LINE 連線
  python cli.py test-gemini                # 測試所有 Gemini Key + OpenRouter
  python cli.py update-knowledge           # 更新知識庫快取
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="分析圖片")
    p_analyze.add_argument("image", help="圖片路徑")
    p_analyze.add_argument("--context", "-c", help="額外說明文字")
    p_analyze.add_argument("--push-to", help="分析後推送到指定 LINE 使用者 ID")
    p_analyze.set_defaults(func=cmd_analyze)

    # history
    p_history = subparsers.add_parser("history", help="查看分析歷史")
    p_history.add_argument("--limit", "-n", type=int, default=10, help="顯示筆數")
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
    p_update = subparsers.add_parser("update-knowledge", help="更新知識庫快取")
    p_update.set_defaults(func=cmd_update_knowledge)

    # check
    p_check = subparsers.add_parser("check", help="檢查環境設定")
    p_check.set_defaults(func=cmd_check)

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
