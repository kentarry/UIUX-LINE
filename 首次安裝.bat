@echo off
echo.
echo ======================================
echo   LINE 美術圖審查工具 - 首次安裝
echo ======================================
echo.

cd /d "%~dp0"

:: 檢查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 找不到 Python，請先安裝 Python 3.10+
    echo    下載: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [安裝] 安裝依賴套件...
pip install -r requirements.txt

echo.
echo [完成] 安裝完成！
echo.
echo 下一步：
echo   1. 複製 .env.example 為 .env
echo   2. 填入 LINE 和 Gemini API Keys
echo   3. 執行 start.bat 啟動伺服器
echo.

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo 已建立 .env，請編輯填入 API Keys：
    notepad ".env"
)

pause
