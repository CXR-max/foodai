param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$Python = Join-Path $Backend '.venv\Scripts\python.exe'
$Vite = Join-Path $Frontend 'node_modules\vite\bin\vite.js'

# VSCode 任务/新终端的 PATH 可能没有 Node（常见于装完 Node 后没重启 VSCode）。
# 不能只查 Get-Command，必须回退到标准安装目录。
function Find-Node {
    $cmd = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path $cmd.Source)) {
        return $cmd.Source
    }
    $candidates = @(
        (Join-Path $env:ProgramFiles 'nodejs\node.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\nodejs\node.exe'),
        (Join-Path $env:APPDATA 'nvm\node.exe'),
        (Join-Path $env:LOCALAPPDATA 'Volta\bin\node.exe')
    )
    $x86 = ${env:ProgramFiles(x86)}
    if ($x86) {
        $candidates += (Join-Path $x86 'nodejs\node.exe')
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    return $null
}

$Node = Find-Node
if ($Node) {
    # 让 Vite/esbuild 的子进程也能找到 node
    $env:Path = "$(Split-Path -Parent $Node);$env:Path"
}
$Url = 'http://127.0.0.1:5173/'

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Test-Port {
    param([int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        if (-not $task.Wait(400)) { return $false }
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Wait-Port {
    param([int]$Port, [int]$TimeoutSec = 45)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port $Port) { return }
        Start-Sleep -Milliseconds 400
    }
    throw "端口 $Port 在 $TimeoutSec 秒内没有就绪，请查看新打开的服务窗口日志。"
}

function Test-Http {
    param([string]$Target)
    $code = & curl.exe -s -m 4 --noproxy '*' -o NUL -w '%{http_code}' $Target
    return ($LASTEXITCODE -eq 0 -and $code -eq '200')
}

Write-Host '===== 识膳智行 一键启动 =====' -ForegroundColor Cyan

# ---- 本地 Ollama 11434 ----
$OllamaCandidates = @()
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd) { $OllamaCandidates += $ollamaCmd.Source }
$OllamaCandidates += @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
    (Join-Path $env:ProgramFiles 'Ollama\ollama.exe')
)
$Ollama = $OllamaCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (Test-Port 11434) {
    Write-Host '[Ollama] 11434 已在运行，直接复用' -ForegroundColor Yellow
} elseif ($Ollama) {
    Write-Host '[Ollama] 正在启动本地模型服务（新窗口）...' -ForegroundColor Green
    Start-Process -FilePath $Ollama -ArgumentList 'serve' -WindowStyle Normal
    Wait-Port 11434
    Write-Host '[Ollama] 11434 已就绪' -ForegroundColor Green
} else {
    Write-Host '[Ollama] 未安装，聊天会走 mock 兜底' -ForegroundColor Yellow
}

# ---- 后端 8000 ----
if (Test-Port 8000) {
    if (-not (Test-Http 'http://127.0.0.1:8000/api/health')) {
        throw '8000 端口已被其他程序占用，但不是本项目的后端。请先关闭占用程序。'
    }
    Write-Host '[后端] 8000 已在运行，直接复用' -ForegroundColor Yellow
} else {
    if (-not (Test-Path $Python)) {
        throw "找不到后端 Python 解释器：$Python"
    }
    Write-Host '[后端] 正在启动 FastAPI（新窗口）...' -ForegroundColor Green
    Start-Process -FilePath $Python `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--reload') `
        -WorkingDirectory $Backend -WindowStyle Normal
    Wait-Port 8000
    Write-Host '[后端] 8000 已就绪' -ForegroundColor Green
}

# ---- 前端 5173 ----
if (Test-Port 5173) {
    if (-not (Test-Http $Url)) {
        throw '5173 端口已被其他程序占用，但不是本项目的前端。请先关闭占用程序。'
    }
    Write-Host '[前端] 5173 已在运行，直接复用' -ForegroundColor Yellow
} else {
    if (-not $Node) {
        throw '找不到 node.exe，请确认 Node.js 已安装并加入 PATH。'
    }
    if (-not (Test-Path $Vite)) {
        throw "找不到 Vite：$Vite，请先在 frontend 目录执行 npm install。"
    }
    Write-Host '[前端] 正在启动 Vite（新窗口）...' -ForegroundColor Green
    Start-Process -FilePath $Node `
        -ArgumentList @($Vite, '--host', '127.0.0.1', '--port', '5173') `
        -WorkingDirectory $Frontend -WindowStyle Normal
    Wait-Port 5173
    Write-Host '[前端] 5173 已就绪' -ForegroundColor Green
}

if ($NoBrowser) {
    Write-Host "启动页：$Url" -ForegroundColor Cyan
} else {
    Write-Host "正在打开浏览器：$Url" -ForegroundColor Cyan
    Start-Process $Url
}
