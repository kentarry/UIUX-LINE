@echo off
chcp 65001 >nul
echo.
echo ══════════════════════════════════════
echo   NotebookLM → 知識庫同步 一鍵執行
echo ══════════════════════════════════════
echo.

cd /d "%~dp0"

:: 同步知識庫並自動推送到雲端
python sync_knowledge.py --push

echo.
pause
