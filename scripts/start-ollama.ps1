param([string]$Model = "qwen2.5:7b")

$ErrorActionPreference = 'Stop'

$env:OLLAMA_CONTEXT_LENGTH = '8192'
$env:OLLAMA_KEEP_ALIVE = '-1'
$ModelDir = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:USERPROFILE '.ollama\models' }

$candidates = @()
$cmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($cmd) { $candidates += $cmd.Source }
$candidates += @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
    (Join-Path $env:ProgramFiles 'Ollama\ollama.exe')
)
$Ollama = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $Ollama) {
    Write-Host '未找到 Ollama。' -ForegroundColor Red
    Write-Host '请先从 https://ollama.com/download 安装 Ollama，然后执行：' -ForegroundColor Yellow
    Write-Host "  ollama pull $Model" -ForegroundColor Yellow
    Write-Host '安装完成后重新运行本脚本。' -ForegroundColor Yellow
    exit 1
}

$client = [System.Net.Sockets.TcpClient]::new()
$running = $false
try {
    $task = $client.ConnectAsync('127.0.0.1', 11434)
    if ($task.Wait(400)) { $running = $client.Connected }
} catch {
    $running = $false
} finally {
    $client.Dispose()
}

if ($running) {
    Write-Host 'Ollama 已在 11434 运行。' -ForegroundColor Yellow
    Write-Host "如果还没拉取模型，请执行：ollama pull $Model" -ForegroundColor Yellow
    exit 0
}

Write-Host "Ollama: $Ollama" -ForegroundColor Green
Write-Host "模型目录: $ModelDir" -ForegroundColor Green
Write-Host "首次使用请另开一个终端执行：ollama pull $Model" -ForegroundColor Yellow
Write-Host '启动 Ollama serve ...' -ForegroundColor Green
& $Ollama serve
