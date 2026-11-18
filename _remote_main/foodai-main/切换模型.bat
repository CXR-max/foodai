@echo off
title 识膳智行 - 切换大模型
set "PYTHONIOENCODING=gbk"
cd /d "%~dp0backend"

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到 backend\.venv，请先完成部署
    pause
    exit /b 1
)

:menu
cls
echo ============================================================
echo              识膳智行 - 大模型切换
echo ============================================================
echo.
.venv\Scripts\python.exe switch_llm.py status
echo.
echo ------------------------------------------------------------
echo   [1] 智谱 GLM     云端免费档 glm-4-flash + glm-4v-flash
echo   [2] 千问 Qwen    阿里云百炼 qwen-plus + qwen-vl-plus
echo   [3] 本地千问     文字走本机 Qwen2.5-7B，视觉仍走智谱
echo   [4] 模拟模式     不联网、不花钱，全部本地模拟
echo   [5] 退出
echo ------------------------------------------------------------
echo.
set /p "sel=请选择 [1-5]: "

if "%sel%"=="1" goto zhipu
if "%sel%"=="2" goto qwen
if "%sel%"=="3" goto local
if "%sel%"=="4" goto mock
if "%sel%"=="5" exit /b 0
goto menu

:zhipu
echo.
echo 智谱 API Key 申请：https://open.bigmodel.cn  —  注册后在「API Keys」创建
set /p "k=粘贴智谱 Key（已保存过就直接回车）: "
if "%k%"=="" (
    .venv\Scripts\python.exe switch_llm.py zhipu
) else (
    .venv\Scripts\python.exe switch_llm.py zhipu %k%
)
goto done

:qwen
echo.
echo 千问 API Key 申请：https://bailian.console.aliyun.com  —  开通「百炼」后创建 API-KEY
set /p "k=粘贴百炼 Key（已保存过就直接回车）: "
if "%k%"=="" (
    .venv\Scripts\python.exe switch_llm.py qwen
) else (
    .venv\Scripts\python.exe switch_llm.py qwen %k%
)
goto done

:local
echo.
echo 提示：本地模式需要 11434 端口的本地千问服务（双击 启动本地模型.bat 即可）
set /p "m=模型名（直接回车=默认 qwen2.5:7b-instruct）: "
if "%m%"=="" (
    .venv\Scripts\python.exe switch_llm.py local
) else (
    .venv\Scripts\python.exe switch_llm.py local %m%
)
goto done

:mock
.venv\Scripts\python.exe switch_llm.py mock
goto done

:done
echo.
echo ------------------------------------------------------------
echo Key 会按厂商分别保存，下次切换同一家不用再粘贴。
echo 切换后必须重启后端：关掉「识膳智行-后端 8000」窗口，
echo 再双击 D:\foodai\启动.bat（或只重启后端那一个窗口）。
echo ------------------------------------------------------------
pause
goto menu
