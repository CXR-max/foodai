@echo off
title 识膳智行 - 本地千问 (Ollama, 127.0.0.1:11434)
cd /d "%~dp0"

rem 本地文字模型统一用 Ollama（模型在仓库根目录 models/，已 gitignore）
rem 旧的 llama.cpp 方案（tools\llama + models\gguf）已废弃

where ollama >nul 2>nul
if errorlevel 1 if not exist "E:\app\ollama\ollama.exe" (
    echo [错误] 未找到 Ollama，请先安装 https://ollama.com/download
    pause
    exit /b 1
)

echo 启动 Ollama serve（关闭本窗口即停止）...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-ollama.ps1"

if errorlevel 1 pause
