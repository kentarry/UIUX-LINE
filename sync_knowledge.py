"""
LINE 美術圖審查工具 — NotebookLM 知識同步腳本

從 NotebookLM 自動萃取知識，更新本地知識庫，並推送到雲端。

使用方式：
  python sync_knowledge.py                    # 互動模式
  python sync_knowledge.py --headed           # 顯示瀏覽器（除錯用）
  python sync_knowledge.py --login            # 先登入 Google
  python sync_knowledge.py --push             # 同步後自動 git push
  python sync_knowledge.py --notebook-url URL # 指定 NotebookLM 網址

運作流程：
  1. 開啟瀏覽器，載入你的 NotebookLM 筆記本
  2. 自動在對話框輸入萃取指令
  3. 等待 NotebookLM 生成回應
  4. 爬取回應內容，寫入 knowledge/ 目錄
  5. (選用) 自動 git commit + push → Render 自動重新部署
"""
import sys
import os
import asyncio
import argparse
import logging
import re
from pathlib import Path
from datetime import datetime

# Windows console UTF-8 支援
if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ── 設定 ──
BASE_DIR = Path(__file__).parent
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
PROFILE_DIR = Path(os.environ.get(
    "LOCALAPPDATA", Path.home() / "AppData" / "Local"
)) / "playwright-line-art-reviewer"

# 預設 NotebookLM URL（請替換為你自己的筆記本網址）
DEFAULT_NOTEBOOK_URL = os.environ.get(
    "NOTEBOOKLM_URL",
    ""  # 需要使用者設定
)

# ── 萃取 Prompt（告訴 NotebookLM 輸出什麼格式）──
EXTRACT_PROMPTS = {
    "project_specific": {
        "file": "project_specific.md",
        "prompt": """請根據目前上傳的所有資料，幫我整理出一份完整的「專案設計規範」。

輸出格式要求：
1. 使用 Markdown 格式
2. 開頭加上 `# 專案特定設計規範`
3. 分類整理（品牌色彩、字型、間距、按鈕、圖示、特殊規則等）
4. 每個規則要具體、可量化（例如：「主色 #FF6B00」而非「使用暖色調」）
5. 只輸出規範內容，不要加前言或結語

請直接輸出 Markdown 內容：""",
    },
    "common_issues": {
        "file": "common_issues.md",
        "prompt": """請根據目前上傳的所有資料，幫我整理出一份「常見設計審查問題庫」。

輸出格式要求：
1. 使用 Markdown 格式
2. 開頭加上 `# 常見審查問題庫`
3. 每個問題包含：問題描述、影響等級（🔴高/🟡中/🟢低）、標準修正方式
4. 按照影響等級排序（高→低）
5. 只根據資料中實際出現過的問題，不要自行補充
6. 只輸出問題庫內容，不要加前言或結語

請直接輸出 Markdown 內容：""",
    },
    "review_examples": {
        "file": "review_examples.md",
        "prompt": """請根據目前上傳的所有資料，幫我整理出一份「設計審查範例對照表」。

輸出格式要求：
1. 使用 Markdown 格式
2. 開頭加上 `# 審查範例對照`
3. 分為「✅ 優秀設計範例」和「❌ 需修正範例」兩大區塊
4. 每個範例包含：描述、為什麼好/為什麼不好、正確做法
5. 只根據資料中實際出現過的案例，不要自行補充
6. 只輸出範例內容，不要加前言或結語

請直接輸出 Markdown 內容：""",
    },
}


async def login_google(headed: bool = True):
    """開啟瀏覽器讓使用者手動登入 Google"""
    from playwright.async_api import async_playwright

    logger.info("開啟瀏覽器，請手動登入 Google 帳號...")
    logger.info(f"瀏覽器 Profile: {PROFILE_DIR}")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,  # 登入一定要顯示瀏覽器
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-default-apps",
            ],
            viewport={"width": 1280, "height": 900},
            locale="zh-TW",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://accounts.google.com", wait_until="load")

        print()
        print("═" * 50)
        print("  請在瀏覽器中登入你的 Google 帳號")
        print("  登入完成後，按 Enter 繼續...")
        print("═" * 50)
        input()

        # 驗證登入狀態
        await page.goto(
            "https://notebooklm.google.com",
            wait_until="load",
            timeout=30000
        )
        await page.wait_for_timeout(5000)

        current_url = page.url
        if "accounts.google.com" in current_url:
            print("⚠️ 似乎尚未成功登入，請重新執行 --login")
        else:
            print("✅ Google 登入成功！NotebookLM 可正常存取。")

        await context.close()


async def extract_from_notebooklm(
    notebook_url: str,
    extract_keys: list[str] = None,
    headed: bool = False,
):
    """
    從 NotebookLM 萃取知識

    Args:
        notebook_url: NotebookLM 筆記本 URL
        extract_keys: 要萃取的知識類型（預設全部）
        headed: 是否顯示瀏覽器
    """
    from playwright.async_api import async_playwright

    if not extract_keys:
        extract_keys = list(EXTRACT_PROMPTS.keys())

    if not PROFILE_DIR.exists():
        logger.error(
            f"找不到瀏覽器 Profile: {PROFILE_DIR}\n"
            "請先執行 python sync_knowledge.py --login 登入 Google"
        )
        return False

    logger.info(f"開始萃取知識 ({len(extract_keys)} 項)")
    logger.info(f"NotebookLM: {notebook_url}")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=not headed,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-default-apps",
            ],
            viewport={"width": 1280, "height": 900},
            locale="zh-TW",
        )

        page = context.pages[0] if context.pages else await context.new_page()

        try:
            # 載入 NotebookLM
            logger.info("載入 NotebookLM...")
            await page.goto(notebook_url, wait_until="load", timeout=60000)
            await page.wait_for_timeout(8000)  # SPA 載入需要時間

            # 檢查是否需要登入
            if "accounts.google.com" in page.url:
                logger.error(
                    "Google 登入已過期！"
                    "請先執行 python sync_knowledge.py --login"
                )
                return False

            logger.info("NotebookLM 載入完成")

            # 逐項萃取
            for key in extract_keys:
                config = EXTRACT_PROMPTS[key]
                logger.info(f"正在萃取: {key} → {config['file']}")

                success = await _send_and_extract(
                    page, config["prompt"], config["file"]
                )

                if success:
                    logger.info(f"✅ {key} 萃取完成")
                else:
                    logger.error(f"❌ {key} 萃取失敗")

                # 等待一下再進行下一項
                await page.wait_for_timeout(3000)

            return True

        except Exception as e:
            logger.error(f"萃取過程發生錯誤: {e}", exc_info=True)
            return False

        finally:
            await context.close()


async def _send_and_extract(page, prompt: str, output_file: str) -> bool:
    """
    在 NotebookLM 對話框輸入 prompt，等待回應，萃取內容

    Args:
        page: Playwright Page
        prompt: 萃取指令
        output_file: 輸出檔名

    Returns:
        是否成功
    """
    try:
        # 1. 找到對話輸入框
        # NotebookLM 的輸入框可能是 textarea 或 contenteditable
        input_box = None

        # 嘗試多種選擇器
        selectors = [
            'textarea[aria-label*="輸入"]',
            'textarea[aria-label*="Enter"]',
            'textarea[placeholder*="輸入"]',
            'textarea[placeholder*="Type"]',
            '[contenteditable="true"]',
            'textarea',
        ]

        for sel in selectors:
            locator = page.locator(sel)
            if await locator.count() > 0:
                input_box = locator.first
                logger.info(f"找到輸入框: {sel}")
                break

        if not input_box:
            logger.error("找不到 NotebookLM 對話輸入框")
            return False

        # 2. 輸入萃取 prompt
        await input_box.click()
        await page.wait_for_timeout(500)

        # 用 fill 比 type 快
        await input_box.fill(prompt)
        await page.wait_for_timeout(1000)

        # 3. 送出（Enter 或按鈕）
        send_btn = page.locator(
            'button[aria-label*="傳送"], '
            'button[aria-label*="Send"], '
            'button[aria-label*="送出"]'
        )

        if await send_btn.count() > 0:
            await send_btn.first.click()
        else:
            await page.keyboard.press("Enter")

        logger.info("已送出萃取指令，等待 NotebookLM 回應...")

        # 4. 等待回應完成（偵測「正在輸入」指示器消失）
        # 最長等待 120 秒
        max_wait = 120
        poll_interval = 3
        waited = 0

        await page.wait_for_timeout(5000)  # 先等 5 秒讓回應開始

        while waited < max_wait:
            # 檢查是否還在生成中
            loading_indicators = [
                '.loading-indicator',
                '[class*="loading"]',
                '[class*="typing"]',
                '[class*="generating"]',
                '.response-loading',
            ]

            still_loading = False
            for indicator in loading_indicators:
                count = await page.locator(indicator).count()
                if count > 0:
                    is_visible = await page.locator(indicator).first.is_visible()
                    if is_visible:
                        still_loading = True
                        break

            if not still_loading:
                # 額外等待確認已完全停止
                await page.wait_for_timeout(3000)
                break

            await page.wait_for_timeout(poll_interval * 1000)
            waited += poll_interval
            logger.info(f"等待回應中... ({waited}/{max_wait}s)")

        # 5. 萃取最後一個回應的內容
        # NotebookLM 的回應通常在特定容器中
        response_selectors = [
            '.response-container:last-child',
            '.message-content:last-child',
            '[class*="response"]:last-child',
            '[class*="answer"]:last-child',
            '.chat-message:last-child .message-body',
        ]

        response_text = ""

        # 方法 1：嘗試特定選擇器
        for sel in response_selectors:
            locator = page.locator(sel)
            if await locator.count() > 0:
                response_text = await locator.last.inner_text()
                if len(response_text) > 100:  # 確保內容夠長
                    logger.info(f"用選擇器 {sel} 取得回應 ({len(response_text)} 字元)")
                    break

        # 方法 2：如果特定選擇器沒找到，用更通用的方式
        if len(response_text) < 100:
            # 取得所有 chat 訊息，找最後一個 AI 回應
            all_messages = page.locator(
                '[class*="message"], [class*="response"], '
                '[class*="chat-turn"], [class*="conversation-turn"]'
            )
            count = await all_messages.count()
            if count > 0:
                last_msg = all_messages.nth(count - 1)
                response_text = await last_msg.inner_text()
                logger.info(f"用通用選擇器取得回應 ({len(response_text)} 字元)")

        # 方法 3：如果還是沒有，用剪貼簿
        if len(response_text) < 100:
            # 嘗試找「複製」按鈕
            copy_btns = page.locator(
                'button[aria-label*="複製"], '
                'button[aria-label*="Copy"], '
                'button[title*="複製"], '
                'button[title*="Copy"]'
            )
            if await copy_btns.count() > 0:
                await copy_btns.last.click()
                await page.wait_for_timeout(1000)
                # 讀取剪貼簿
                response_text = await page.evaluate(
                    "navigator.clipboard.readText()"
                )
                logger.info(f"用剪貼簿取得回應 ({len(response_text)} 字元)")

        if len(response_text) < 50:
            logger.error(
                f"回應內容過短 ({len(response_text)} 字元)，可能萃取失敗"
            )
            return False

        # 6. 清理內容
        response_text = _clean_response(response_text)

        # 7. 寫入知識庫檔案
        output_path = KNOWLEDGE_DIR / output_file
        
        # 備份舊檔
        if output_path.exists():
            backup_name = f"{output_path.stem}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}{output_path.suffix}"
            backup_path = KNOWLEDGE_DIR / backup_name
            output_path.rename(backup_path)
            logger.info(f"舊檔已備份: {backup_name}")

        # 加上更新時間戳
        header = (
            f"> 此檔案由 sync_knowledge.py 自動從 NotebookLM 萃取\n"
            f"> 最後更新：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"> 請勿手動編輯，修改會被下次同步覆蓋\n\n"
        )
        output_path.write_text(header + response_text, encoding="utf-8")
        logger.info(f"已寫入: {output_path} ({len(response_text)} 字元)")

        return True

    except Exception as e:
        logger.error(f"萃取單項失敗: {e}", exc_info=True)
        return False


def _clean_response(text: str) -> str:
    """清理 NotebookLM 回應中的雜訊"""
    # 移除開頭/結尾的空白
    text = text.strip()

    # 移除 NotebookLM 可能加的前綴
    prefixes_to_remove = [
        "好的，以下是",
        "以下是根據",
        "根據上傳的資料",
    ]
    for prefix in prefixes_to_remove:
        if text.startswith(prefix):
            # 找到第一個 # 開頭的行
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if line.strip().startswith("#"):
                    text = "\n".join(lines[i:])
                    break
            break

    return text


def git_push(message: str = None):
    """自動 git commit + push"""
    import subprocess

    if not message:
        message = f"sync: 更新知識庫 {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    try:
        # git add knowledge/
        subprocess.run(
            ["git", "add", "knowledge/"],
            cwd=str(BASE_DIR),
            check=True,
            capture_output=True
        )

        # git commit
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            logger.info(f"Git commit: {message}")
        else:
            if "nothing to commit" in result.stdout:
                logger.info("知識庫無變更，跳過 commit")
                return True
            logger.error(f"Git commit 失敗: {result.stderr}")
            return False

        # git push
        result = subprocess.run(
            ["git", "push"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            logger.info("✅ Git push 成功！Render 將自動重新部署。")
            return True
        else:
            logger.error(f"Git push 失敗: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"Git 操作失敗: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(
        description="從 NotebookLM 同步知識庫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用範例:
  python sync_knowledge.py --login            # 首次：登入 Google
  python sync_knowledge.py --headed           # 同步（顯示瀏覽器）
  python sync_knowledge.py --push             # 同步並自動推送到雲端
  python sync_knowledge.py --only project_specific  # 只同步專案規範
        """
    )

    parser.add_argument(
        "--login", action="store_true",
        help="開啟瀏覽器登入 Google（首次使用或 Session 過期時）"
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="顯示瀏覽器視窗（除錯用）"
    )
    parser.add_argument(
        "--push", action="store_true",
        help="同步後自動 git commit + push"
    )
    parser.add_argument(
        "--notebook-url",
        default=DEFAULT_NOTEBOOK_URL,
        help="NotebookLM 筆記本 URL"
    )
    parser.add_argument(
        "--only", nargs="+",
        choices=list(EXTRACT_PROMPTS.keys()),
        help="只同步指定的知識類型"
    )

    args = parser.parse_args()

    # 登入模式
    if args.login:
        await login_google(headed=True)
        return

    # 檢查 URL
    if not args.notebook_url:
        print("❌ 請提供 NotebookLM 筆記本 URL")
        print()
        print("設定方式（擇一）：")
        print("  1. 環境變數: NOTEBOOKLM_URL=https://notebooklm.google.com/notebook/...")
        print("  2. 命令參數: --notebook-url https://notebooklm.google.com/notebook/...")
        print("  3. 直接編輯本檔案的 DEFAULT_NOTEBOOK_URL")
        sys.exit(1)

    # 同步
    print()
    print("═" * 50)
    print("  NotebookLM → 知識庫同步")
    print("═" * 50)
    print()

    success = await extract_from_notebooklm(
        notebook_url=args.notebook_url,
        extract_keys=args.only,
        headed=args.headed,
    )

    if success:
        print()
        print("✅ 知識庫同步完成！")

        if args.push:
            print()
            print("📤 推送到 Git...")
            git_push()
        else:
            print()
            print("💡 提示：加上 --push 可自動推送到雲端")
            print("   python sync_knowledge.py --push")
    else:
        print()
        print("❌ 同步失敗，請檢查日誌")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
