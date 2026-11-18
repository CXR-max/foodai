@echo off
setlocal enabledelayedexpansion
title 识膳智行 - 手机访问模式
cd /d "%~dp0"

echo ============================================
echo   识膳智行 - 手机访问模式（局域网 HTTPS）
echo ============================================
echo.

if not exist "%~dp0backend\.venv\Scripts\python.exe" (
    echo [错误] 未找到后端虚拟环境 backend\.venv
    pause
    exit /b 1
)

if not exist "%~dp0frontend\node_modules" (
    echo [错误] 未找到前端依赖 frontend\node_modules
    echo 请先在 frontend 目录执行 npm install
    pause
    exit /b 1
)

rem ---- 先释放端口，避免 WinError 10048 ----
echo [0/4] 释放 8000 / 5173 端口...
call :killport 8000
call :killport 5173
echo.

rem ---- 探测局域网 IPv4（必须有默认网关，排除 VMware/VirtualBox 等虚拟网卡） ----
set "LAN="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "Get-NetIPConfiguration ^| Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' -and $_.InterfaceAlias -notmatch 'VMware^|VirtualBox^|vEthernet^|Mihomo^|Loopback' } ^| Select-Object -First 1 -ExpandProperty IPv4Address ^| Select-Object -ExpandProperty IPAddress"`) do set "LAN=%%i"

if "%LAN%"=="" (
    for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 ^| Where-Object { $_.IPAddress -match '^(192\.168|10\.|172\.(1[6-9]^|2[0-9]^|3[01])\.)' -and $_.IPAddress -notmatch '^192\.168\.(56|111|238)\.1$' } ^| Select-Object -First 1 -ExpandProperty IPAddress)"`) do set "LAN=%%i"
)

if "%LAN%"=="" (
    echo [错误] 没找到局域网 IPv4 地址，请确认电脑已连 Wi-Fi / 网线
    pause
    exit /b 1
)

echo 本机局域网 IP: %LAN%
echo.

echo [1/4] 启动后端 FastAPI（监听 0.0.0.0:8000，手机可直连）
start "识膳智行-后端 8000" cmd /k "cd /d %~dp0backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
call :waiturl http://127.0.0.1:8000/api/health "后端" 0

echo [2/4] 启动前端 Vite（0.0.0.0 + 自签名 HTTPS）
start "识膳智行-前端 5173" cmd /k "cd /d %~dp0frontend && set VITE_HOST=0.0.0.0&& set VITE_HTTPS=1&& npm run dev"
call :waiturl https://127.0.0.1:5173 "前端(https)" 1
echo.

cls
echo ============================================
echo   访问地址（手机和电脑连同一个 Wi-Fi）
echo ============================================
echo.
echo   手机浏览器：
echo       https://%LAN%:5173
echo.
echo   电脑浏览器（注意是 https，不是 http）：
echo       https://localhost:5173
echo       ^(http://localhost:5173 打不开是正常的，本模式只开了 https^)
echo.
echo --------------------------------------------
echo   第一次打开会提示"不是私密连接 / 不安全"：
echo   点 [高级] - [继续前往] 即可（自签名证书，局域网内安全）
echo.
echo   微信里打开会没有蓝牙，请点右上角 ... - 在浏览器打开
echo ============================================
echo.
echo 两个黑色窗口请保持开启，关闭窗口即停止服务。
echo.
start "" "https://localhost:5173"
pause
exit /b 0

rem ============ 子程序：释放端口（最多等 5 秒） ============
:killport
set "P=%~1"
set /a KP_TRY=0
:kp_loop
set "KP_FOUND="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%P% " 2^>nul') do (
    set "KP_FOUND=1"
    taskkill /F /PID %%a >nul 2>nul
)
if not defined KP_FOUND goto :eof
set /a KP_TRY+=1
if !KP_TRY! geq 5 (
    echo   [警告] 端口 %P% 仍被占用，请手动关掉对应黑窗口后重试
    goto :eof
)
timeout /t 1 /nobreak >nul
goto :kp_loop

rem ============ 子程序：等待 URL 就绪 ============
:waiturl
set "WU_URL=%~1"
set "WU_NAME=%~2"
set "WU_INSECURE=%~3"
set /a WU_TRY=0
:wu_loop
if "%WU_INSECURE%"=="1" (
    curl -sk --noproxy "*" -o nul --max-time 2 "%WU_URL%"
) else (
    curl -s --noproxy "*" -o nul --max-time 2 "%WU_URL%"
)
if not errorlevel 1 (
    echo       %WU_NAME% 已就绪
    goto :eof
)
set /a WU_TRY+=1
if !WU_TRY! geq 30 (
    echo       [警告] %WU_NAME% 30 秒内未就绪，请查看对应的黑窗口报错
    goto :eof
)
timeout /t 1 /nobreak >nul
goto :wu_loop
