# 启动本地 Ollama 服务（文字模型走本地；视觉仍走云端）
# 用法：在 PowerShell 里执行  .\scripts\start-ollama.ps1
# 模型文件统一存放在项目内 E:\allprograms\foodai\models，随项目走

$env:OLLAMA_MODELS = "E:\allprograms\foodai\models"   # 模型存放位置（项目内）
$env:OLLAMA_CONTEXT_LENGTH = "8192"                   # 上下文长度
$env:OLLAMA_KEEP_ALIVE = "-1"                         # 模型常驻显存，首次加载后不再等

$ollama = "E:\app\ollama\ollama.exe"
if (-not (Test-Path $ollama)) {
    Write-Host "未找到 $ollama，请先把 Ollama 解压到 E:\app\ollama" -ForegroundColor Red
    exit 1
}

Write-Host "Ollama 启动中… 模型目录: $env:OLLAMA_MODELS" -ForegroundColor Green
& $ollama serve
