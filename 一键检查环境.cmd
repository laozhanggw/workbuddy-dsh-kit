@echo off
chcp 936 >nul
cd /d "%~dp0"
title WorkBuddy Self Check
set PY=D:\Python 3.12.3\python.exe
if not exist "%PY%" set PY=python
set GW=%~dp0..\WorkBuddy2API
set MAG=%USERPROFILE%\.config\magpie\providers.json

echo ============================================================
echo   WorkBuddy 方案 - 环境自检
echo ============================================================
echo.

echo [1/6] Python
"%PY%" --version
if errorlevel 1 (echo     X 没找到 Python，请装 3.12 或改本文件开头的 PY 路径) else (echo     OK)
echo.

echo [2/6] 依赖 segno
"%PY%" -c "import segno,sys;print('     segno',segno.__version__)"
if errorlevel 1 (echo     X 缺 segno：pip install segno) else (echo     OK)
echo.

echo [3/6] 网关目录
if exist "%GW%\wb_proxy.py" (echo     OK  %GW%) else (echo     X 没找到 wb_proxy.py，检查 GW 路径)
echo.

echo [4/6] 网关是否在监听 8788
powershell -NoProfile -Command "if (Get-NetTCPConnection -State Listen -LocalPort 8788 -ErrorAction SilentlyContinue) { '     OK  8788 正在监听' } else { '     X   8788 没在跑' }"
echo.

echo [5/6] 批量加号台是否在监听 8790
powershell -NoProfile -Command "if (Get-NetTCPConnection -State Listen -LocalPort 8790 -ErrorAction SilentlyContinue) { '     OK  8790 正在监听' } else { '     -   8790 没起（不影响主流程）' }"
echo.

echo [6/6] Magpie 配置
if exist "%MAG%" (echo     OK  %MAG%) else (echo     -   没找到 %MAG%，先启动一次 Magpie)
echo.

echo ============================================================
echo   自检结束。X 是要修的，- 是可选的。
echo ============================================================
pause
