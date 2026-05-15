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
# 目前分析流程只使用 design_rules.md（已手動維護）和遊戲專屬 .md，
# 不再自動萃取 project_specific / common_issues / review_examples。
# 如需新增萃取目標，請按以下格式加入：
#   "key_name": {
#       "file": "output_filename.md",
#       "prompt": "萃取指令文字",
#   }
EXTRACT_PROMPTS = {
    "東南亞市場": {
        "url": "https://notebooklm.google.com/notebook/3a98de78-a83e-47e3-b416-8b8c4f8ea08a?authuser=6",
        "file": "東南亞市場.md",
        "prompt": "請根據筆記本中的所有來源資料，幫我整理「東南亞市場（包含金銀島、TADA等產品）」的完整 UI/UX 設計審查知識庫。請用以下 Markdown 結構輸出：\n\n# 東南亞市場 — 遊戲 UI/UX 審查知識庫\n## 產品簡介（列出涵蓋的遊戲/App名稱、類型、目標市場）\n## UI 風格特徵（色調、強調色、字體風格、圖標風格等）\n## 設計規範與原則（顧問建議的具體規則，例如：對稱性、顏色使用、文字重疊標準、背景明暗等）\n## 常見設計問題與退回原因\n## 審查重點清單\n\n請將所有來源中提到的具體規範、數值標準、顧問反饋重點都納入，不要遺漏。直接輸出 Markdown 內容，不需要問候語或結語。",
    },
    "競技麻將2": {
        "url": "https://notebooklm.google.com/notebook/b11362de-e39b-4189-96e6-e557b854b137?authuser=6",
        "file": "競技麻將2.md",
        "prompt": "請根據筆記本中的所有來源資料，幫我整理「競技麻將2」的完整 UI/UX 設計審查知識庫。請用以下 Markdown 結構輸出：\n\n# 競技麻將2 — 遊戲 UI/UX 審查知識庫\n## 產品簡介（遊戲類型、主要玩法、市場、平台）\n## UI 風格特徵（色調、強調色、字體風格、牌面風格等）\n## 常見活動頁面元素\n## 設計規範與原則（具體規則，例如對稱性、顏色使用、排版標準等）\n## 審查重點清單\n\n請將所有來源中提到的具體規範、數值標準、顧問反饋重點都納入，不要遺漏。直接輸出 Markdown 內容，不需要問候語或結語。",
    }
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
            permissions=["clipboard-read", "clipboard-write"],
            viewport={"width": 1280, "height": 900},
            locale="zh-TW",
        )

        page = context.pages[0] if context.pages else await context.new_page()

        try:
            current_url = None

            # 逐項萃取
            for key in extract_keys:
                config = EXTRACT_PROMPTS[key]
                target_url = config.get("url", notebook_url)
                
                # 如果該項目有專屬網址，或是尚未載入預設網址
                if current_url != target_url:
                    logger.info(f"載入 NotebookLM: {target_url}...")
                    await page.goto(target_url, wait_until="load", timeout=60000)
                    await page.wait_for_timeout(8000)  # SPA 載入需要時間
                    
                    # 檢查是否需要登入
                    if "accounts.google.com" in page.url:
                        logger.error(
                            "Google 登入已過期！"
                            "請先執行 python sync_knowledge.py --login"
                        )
                        return False
                    
                    current_url = target_url
                    logger.info("NotebookLM 載入完成")

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
            'textarea[placeholder*="提問或創作內容"]',
            'textarea[aria-label*="提問"]',
            'textarea[aria-label*="輸入"]',
            'textarea[aria-label*="Enter"]',
            'textarea[placeholder*="輸入"]',
            'textarea[placeholder*="Type"]',
            '[contenteditable="true"]',
            'textarea',
        ]

        for sel in selectors:
            # 我們需要找到可見的、真正在下方的主輸入框
            locator = page.locator(sel)
            count = await locator.count()
            for i in range(count):
                el = locator.nth(i)
                if await el.is_visible():
                    # 確認它的位置在下方，或者有正確的 placeholder
                    placeholder = await el.get_attribute("placeholder") or ""
                    if "提問" in placeholder or "輸入" in placeholder or i == count - 1:
                        input_box = el
                        logger.info(f"找到輸入框: {sel} (index: {i})")
                        break
            if input_box:
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

        # 4. 等待回應完成
        # NotebookLM 使用 .thinking-message 表示正在生成
        # AI 回應在 mat-card.to-user-message-card-content 中
        max_wait = 180
        poll_interval = 5
        waited = 0

        await page.wait_for_timeout(10000)  # 先等 10 秒讓回應開始

        while waited < max_wait:
            # 檢查是否仍在 thinking
            thinking = page.locator('.thinking-message')
            thinking_count = await thinking.count()
            is_thinking = False
            if thinking_count > 0:
                is_thinking = await thinking.last.is_visible()

            # 取得最後一個 AI 回應的文字長度
            ai_cards = page.locator('mat-card.to-user-message-card-content')
            card_count = await ai_cards.count()
            current_len = 0
            if card_count > 0:
                try:
                    current_len = len(await ai_cards.last.inner_text())
                except Exception:
                    pass

            if not is_thinking and current_len > 100:
                # 多等一次確認穩定
                await page.wait_for_timeout(3000)
                final_len = len(await ai_cards.last.inner_text())
                if final_len == current_len:
                    logger.info(f"回應已完成 ({final_len} 字元)")
                    break

            await page.wait_for_timeout(poll_interval * 1000)
            waited += poll_interval
            logger.info(f"等待回應中... ({waited}/{max_wait}s, thinking={is_thinking}, 字元={current_len})")

        # 5. 萃取最後一個回應的內容
        response_text = ""

        # 方法 1：直接從 AI 回應卡片讀取 inner_text
        ai_cards = page.locator('mat-card.to-user-message-card-content')
        card_count = await ai_cards.count()
        if card_count > 0:
            response_text = await ai_cards.last.inner_text()
            if len(response_text) > 100:
                logger.info(f"用 mat-card 選擇器取得回應 ({len(response_text)} 字元)")

        # 方法 2：用「將模型回覆複製到剪貼簿」按鈕
        if len(response_text) < 100:
            copy_btns = page.locator('button[aria-label="將模型回覆複製到剪貼簿"]')
            copy_count = await copy_btns.count()
            if copy_count > 0:
                await copy_btns.last.click()
                await page.wait_for_timeout(1000)
                try:
                    response_text = await page.evaluate("navigator.clipboard.readText()")
                    logger.info(f"用剪貼簿取得回應 ({len(response_text)} 字元)")
                except Exception as e:
                    logger.warning(f"剪貼簿讀取失敗: {e}")

        # 方法 3：fallback — 任何含 Copy 的按鈕
        if len(response_text) < 100:
            copy_btns = page.locator(
                'button[aria-label*="複製"], '
                'button[aria-label*="Copy"]'
            )
            if await copy_btns.count() > 0:
                await copy_btns.last.click()
                await page.wait_for_timeout(1000)
                try:
                    response_text = await page.evaluate("navigator.clipboard.readText()")
                    logger.info(f"用 fallback 剪貼簿取得回應 ({len(response_text)} 字元)")
                except Exception as e:
                    logger.warning(f"Fallback 剪貼簿讀取失敗: {e}")

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

    # 移除 NotebookLM UI 殘留文字（按鈕、引用標號等）
    ui_artifacts = [
        "儲存至記事", "copy_all", "thumb_up", "thumb_down",
        "more_horiz", "keep_pin", "content_copy",
    ]
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # 跳過純數字行（NotebookLM 的引用標號）
        if stripped and stripped.isdigit():
            continue
        # 跳過 UI 殘留文字
        if stripped in ui_artifacts:
            continue
        # 跳過孤立的「。」
        if stripped == "。":
            continue
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)

    # 把「句末換行後的獨立標點」合併回前一行
    text = re.sub(r'\n\s*。', '。', text)
    text = re.sub(r'\n\s*？', '？', text)

    # 移除多餘空行（3行以上縮為2行）
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


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

    # 檢查 URL（如果所選項目都有專屬網址，則不需要全域 URL）
    extract_keys = args.only or list(EXTRACT_PROMPTS.keys())
    needs_global_url = any("url" not in EXTRACT_PROMPTS[k] for k in extract_keys)

    if needs_global_url and not args.notebook_url:
        print("❌ 請提供預設 NotebookLM 筆記本 URL（部分項目未指定專屬網址）")
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
