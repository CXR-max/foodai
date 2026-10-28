@echo off
title 识膳智行 - 本地千问 (127.0.0.1:11434)
cd /d "%~dp0"

if not exist "tools\llama\Library\bin\llama-server.exe" (
    echo [错误] 未找到 tools\llama\Library\bin\llama-server.exe
    echo 请先按 部署说明.md 的「本地千问」章节准备运行时
    pause
    exit /b 1
)
if not exist "models\gguf\qwen2.5-7b-instruct-q4_k_m.gguf" (
    echo [错误] 未找到模型文件 models\gguf\qwen2.5-7b-instruct-q4_k_m.gguf
    pause
    exit /b 1
)

echo ============================================================
echo   本地千问 Qwen2.5-7B  -  http://127.0.0.1:11434
echo   模型: D:\foodai\models\gguf\qwen2.5-7b-instruct-q4_k_m.gguf
echo ============================================================
echo.
echo 本窗口请保持开启，关闭即停止本地模型服务。
echo 首次加载约 15 秒，加载完成后再访问前端。
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-local-llm.ps1"
pause
