@echo off
setlocal enabledelayedexpansion
title 识膳智行 - 停止服务
cd /d "%~dp0"

echo ============================================
echo   识膳智行 - 停止服务
echo ============================================
echo.

set /a COUNT=0
for %%P in (8000 5173 11434) do (
    set "HIT=0"
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P" ^| findstr "LISTENING"') do (
        if not defined KILLED_%%A (
            set "KILLED_%%A=1"
            set "HIT=1"
            taskkill /F /PID %%A >nul 2>&1
            if errorlevel 1 (
                echo   [失败] 端口 %%P  PID %%A  -  权限不足，请右键「以管理员身份运行」
            ) else (
                echo   [已停止] 端口 %%P  PID %%A
                set /a COUNT+=1
            )
        )
    )
    if "!HIT!"=="0" echo   [未运行] 端口 %%P
)

echo.
if !COUNT! gtr 0 (
    echo 共停止 !COUNT! 个进程。
) else (
    echo 没有需要停止的进程。
)

rem ---- 停完自检：还有没有在监听的 ----
timeout /t 2 /nobreak >nul
set "LEFT=0"
for %%P in (8000 5173 11434) do (
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P" ^| findstr "LISTENING"') do (
        echo   [警告] 端口 %%P 仍在监听，PID %%A
        set "LEFT=1"
    )
)
if "!LEFT!"=="0" (
    echo 自检通过：8000 / 5173 / 11434 均已释放。
) else (
    echo 自检未通过：上面列出的端口仍在运行，请用管理员身份重试，或直接关掉对应的黑窗口。
)

echo.
echo 提示：表格里显示「运行中」的后台任务，进程被杀后会自动结束；
echo       如果状态没刷新，关掉再打开任务面板即可。
echo.
pause
