@echo off
echo 正在停止背景執行的 LINE 美術圖審查伺服器...
taskkill /F /IM python.exe /T
echo 伺服器已停止。
pause
