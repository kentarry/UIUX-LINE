@echo off
echo.
echo ======================================
echo   LINE 美術圖審查工具 - 一鍵啟動
echo ======================================
echo.

cd /d "%~dp0"

:: 檢查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 找不到 Python，請先安裝 Python 3.10+
    pause
    exit /b 1
)

:: 檢查 .env
if not exist ".env" (
    echo [注意] 找不到 .env 檔案
    echo    正在從 .env.example 複製...
    copy ".env.example" ".env" >nul
    echo    請編輯 .env 填入 API Keys 後重新執行
    echo.
    notepad ".env"
    pause
    exit /b 1
)

:: 檢查環境
echo [檢查] 檢查環境...
python cli.py check
echo.

:: 啟動伺服器
echo [啟動] 啟動伺服器...
python cli.py serve
pause
