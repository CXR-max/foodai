param([switch]$KeepOllama)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot

function Stop-Tree {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
}

function Get-MatchingProcesses {
    param([string]$CommandPattern)
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match $CommandPattern
        }
}

function Stop-MatchingTrees {
    param($Processes)
    $ids = @($Processes | ForEach-Object { [int]$_.ProcessId })
    if (-not $ids.Count) { return 0 }
    $roots = @($Processes | Where-Object { $ids -notcontains [int]$_.ParentProcessId })
    foreach ($proc in $roots) {
        Stop-Tree ([int]$proc.ProcessId)
    }
    return $ids.Count
}

function Stop-PortOwner {
    param([int]$Port, [string[]]$AllowedNames)
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $conn) { return $false }
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    if ($proc -and ($AllowedNames -contains $proc.ProcessName)) {
        Stop-Tree ([int]$proc.Id)
        return $true
    }
    return $false
}

Write-Host '===== 识膳智行 一键停止 =====' -ForegroundColor Cyan

# 前端 Vite（只匹配本项目路径，避免误杀其他 node）
$front = Get-MatchingProcesses 'foodai\\frontend.*vite'
$frontCount = Stop-MatchingTrees $front
if ($frontCount -eq 0) {
    [void](Stop-PortOwner 5173 @('node', 'nodejs'))
}
Write-Host "[前端] 已停止 $frontCount 个进程（5173）" -ForegroundColor Green

# 后端 uvicorn + reload 进程树
$back = Get-MatchingProcesses 'foodai\\backend.*uvicorn.*app\.main:app'
$backCount = Stop-MatchingTrees $back
if ($backCount -eq 0) {
    [void](Stop-PortOwner 8000 @('python', 'python3', 'pythonw'))
}
Write-Host "[后端] 已停止 $backCount 个进程（8000）" -ForegroundColor Green

if ($KeepOllama) {
    Write-Host '[Ollama] 保留运行（-KeepOllama）' -ForegroundColor Yellow
} else {
    $ollamaProcs = Get-MatchingProcesses '(Ollama|ollama).*serve|Programs\\Ollama\\(ollama|ollama app)\.exe'
    $ollamaCount = Stop-MatchingTrees $ollamaProcs
    if ($ollamaCount -eq 0) {
        [void](Stop-PortOwner 11434 @('ollama', 'ollama-app', 'ollama app'))
    }
    Write-Host "[Ollama] 已停止 $ollamaCount 个进程（11434）" -ForegroundColor Green
}

Start-Sleep -Seconds 1
foreach ($port in 8000, 5173, 11434) {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($conn) {
        Write-Host "端口 $port 仍被 PID $($conn.OwningProcess) 占用" -ForegroundColor Yellow
    } else {
        Write-Host "端口 $port 已释放" -ForegroundColor DarkGray
    }
}
Write-Host '停止完成。下次启动：任务「全栈: 一键启动（自动打开浏览器）」。' -ForegroundColor Cyan
