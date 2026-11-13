@echo off
cd /d "%~dp0"
title WorkBuddy Watchdog
echo ============================================================
echo   WorkBuddy 自动巡检
echo   每 15 分钟看一眼，流量掉到付费通道就自动拉回国内
echo   日志: 同目录 watchdog.log
echo   关掉这个窗口 = 停止巡检
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "watchdog_loop.ps1"
pause
