@echo off
echo.
echo ======================================
echo   首次登入 Google (NotebookLM 同步用)
echo ======================================
echo.

cd /d "%~dp0"

:: 安裝 Playwright (如果尚未安裝)
pip install playwright >nul 2>&1
playwright install chromium >nul 2>&1

:: 開啟瀏覽器登入
python sync_knowledge.py --login

pause
