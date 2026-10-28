@echo off
setlocal enabledelayedexpansion
title 识膳智行 - 一键启动
cd /d "%~dp0"

echo ============================================
echo   识膳智行 AI 健康管理平台  -  一键启动
echo ============================================
echo.

if not exist "%~dp0backend\.venv\Scripts\python.exe" (
    echo [错误] 未找到后端虚拟环境：backend\.venv
    echo 请先执行 backend 目录下的安装步骤（见 docs\deployment.md）
    pause
    exit /b 1
)

if not exist "%~dp0frontend\node_modules" (
    echo [错误] 未找到前端依赖：frontend\node_modules
    echo 请先在 frontend 目录执行 npm install
    pause
    exit /b 1
)

rem ---- 读取 .env 里的模型模式 ----
set "MODE=mock"
for /f "tokens=2 delims==" %%a in ('findstr /b /c:"LLM_MODE=" "%~dp0backend\.env" 2^>nul') do set "MODE=%%a"
echo 当前模型模式: !MODE!

if /i "!MODE!"=="local" call :ensure_local
echo.

echo [1/3] 启动后端 FastAPI  (http://localhost:8000)
start "识膳智行-后端 8000" cmd /k "cd /d %~dp0backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

timeout /t 4 /nobreak >nul

echo [2/3] 启动前端 Vite    (http://localhost:5173)
start "识膳智行-前端 5173" cmd /k "cd /d %~dp0frontend && npm run dev"

timeout /t 6 /nobreak >nul

echo [3/3] 打开浏览器
start "" http://localhost:5173

echo.
echo 启动完成。黑色窗口请保持开启，关闭窗口即停止对应服务。
echo   - 前端页面：http://localhost:5173
echo   - 接口文档：http://localhost:8000/docs
echo   - 当前模型：!MODE!  ^(要切换请双击 切换模型.bat^)
echo.
pause
exit /b 0


rem ============ 子程序：确保本地千问在跑 ============
:ensure_local
curl -s --noproxy "*" -o nul --max-time 2 http://127.0.0.1:11434/health
if not errorlevel 1 (
    echo [0/3] 本地千问已在运行
    goto :eof
)

echo [0/3] 本地千问未运行，正在启动（模型 4.7G，首次加载约 20 秒）...
start "识膳智行-本地千问 11434" "%~dp0启动本地模型.bat"

set /a TRIES=0
:wait_loop
timeout /t 3 /nobreak >nul
curl -s --noproxy "*" -o nul --max-time 2 http://127.0.0.1:11434/health
if not errorlevel 1 goto wait_ok
set /a TRIES+=1
if !TRIES! lss 40 goto wait_loop
echo [警告] 本地模型 120 秒内未就绪，先启动前后端；生成方案时可能失败或降级为模拟。
goto :eof

:wait_ok
echo       本地千问已就绪  http://127.0.0.1:11434
goto :eof

