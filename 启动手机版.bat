@echo off
chcp 65001 >nul
title 识膳智行 - 手机端一键启动
cd /d "%~dp0"

echo ============================================
echo   识膳智行 - 手机端(H5) 一键启动
echo ============================================
echo.

if not exist "backend\.venv\Scripts\python.exe" (
  echo [错误] 找不到 backend\.venv
  pause
  exit /b 1
)
if not exist "frontend\node_modules" (
  echo [错误] 找不到 frontend\node_modules，请先在 frontend 目录执行 npm install
  pause
  exit /b 1
)
if not exist "tools\cloudflared.exe" (
  echo [提示] 没找到 tools\cloudflared.exe，手机端无法通过 https 访问
  echo.
)

echo [1/3] 启动后端  http://127.0.0.1:8000
start "foodai-后端" /D "%~dp0backend" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

timeout /t 2 >nul

echo [2/3] 启动前端  http://127.0.0.1:5173
start "foodai-前端" /D "%~dp0frontend" cmd /k "npm run dev"

timeout /t 2 >nul

echo [3/3] 启动手机隧道 ...
start "foodai-隧道" /D "%~dp0" cmd /k "tools\cloudflared.exe tunnel --url http://localhost:5173"

echo.
echo ============================================
echo  等 5~10 秒，看「foodai-隧道」窗口里的这行：
echo     https://xxxx-xxxx.trycloudflare.com
echo  用手机浏览器打开它（Safari 或 Chrome，别用微信/夸克）
echo ============================================
echo.
echo 三个窗口都要保持开着，关掉哪个就断哪个。
pause