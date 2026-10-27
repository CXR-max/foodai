# 启动本地千问服务（llama.cpp 的 llama-server，OpenAI 兼容接口）
# 用法：双击 D:\foodai\启动本地模型.bat，或在 PowerShell 里执行 .\scripts\start-ollama.ps1
#
# 说明：原方案用的是 Ollama，但 Ollama 官网下载包走 GitHub（本机被墙）+ 单文件 1.4G 且大半是
# 用不上的 CUDA 库，所以改成 conda-forge 的 llama.cpp CPU 构建 + ModelScope 的 GGUF 权重，
# 接口同样是 OpenAI 兼容的 /v1/chat/completions，监听端口仍是 11434，项目配置无需改动。

$server = "D:\foodai\tools\llama\Library\bin\llama-server.exe"
$model  = "D:\foodai\models\gguf\qwen2.5-7b-instruct-q4_k_m.gguf"

if (-not (Test-Path $server)) {
    Write-Host "未找到 $server" -ForegroundColor Red
    Write-Host "请先确认 llama.cpp 已解压到 D:\foodai\tools\llama（见 部署说明.md）" -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path $model)) {
    Write-Host "未找到模型文件 $model" -ForegroundColor Red
    exit 1
}

Write-Host "本地千问启动中… 首次加载约 15 秒，请稍候" -ForegroundColor Green
Write-Host "模型: Qwen2.5-7B-Instruct Q4_K_M (4.7G)" -ForegroundColor Gray
Write-Host "地址: http://127.0.0.1:11434   CPU 推理 12 线程，约 8 字/秒" -ForegroundColor Gray
Write-Host "关闭此窗口即停止本地模型服务" -ForegroundColor Gray
Write-Host ""

& $server -m $model -c 8192 -t 12 --host 127.0.0.1 --port 11434 --jinja
