@echo off
REM 启动 Redis（排行榜 ZSet 依赖）。不启动也能用：后端会自动降级 SQLite 查询。
REM Redis 便携版位置：D:\WorkBuddy\redis（若已改位置，请同步修改下面路径）
set REDIS_DIR=D:\WorkBuddy\redis
if not exist "%REDIS_DIR%\redis-server.exe" (
    echo [错误] 未找到 Redis，请确认 %REDIS_DIR%\redis-server.exe 存在
    pause
    exit /b 1
)
echo 正在启动 Redis (127.0.0.1:6379) ...
start "Redis" /min "%REDIS_DIR%\redis-server.exe" --port 6379 --bind 127.0.0.1 --save "" --appendonly no
echo 已启动。可运行以下命令验证： "%REDIS_DIR%\redis-cli.exe" -p 6379 ping
