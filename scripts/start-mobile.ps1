# 手机端一键启动：后端 + 前端 + Cloudflare 隧道
# 用法：VS Code → 终端 → 运行任务 → 「★ 一键启动（网页端 + 手机端）」

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Cloudflared = Join-Path $Root 'tools\cloudflared.exe'
$MetricsUrl = 'http://127.0.0.1:20241/quicktunnel'

Write-Host ''
Write-Host '===== 识膳智行 · 网页端 + 手机端 一键启动 =====' -ForegroundColor Cyan

# ---------- 1. 后端 + 前端 ----------
& (Join-Path $PSScriptRoot 'start-dev.ps1') -NoBrowser

# ---------- 2. 公网隧道 ----------
if (-not (Test-Path $Cloudflared)) {
    Write-Host '[隧道] 找不到 tools\cloudflared.exe' -ForegroundColor Red
    Write-Host '       下载地址：https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -ForegroundColor Yellow
    exit 1
}

$hostname = ''
try { $hostname = (Invoke-RestMethod $MetricsUrl -TimeoutSec 2).hostname } catch { $hostname = '' }

if ($hostname) {
    Write-Host "[隧道] 已有隧道在运行：$hostname" -ForegroundColor Yellow
} else {
    Write-Host '[隧道] 正在建立公网隧道（首次约 5~10 秒）...' -ForegroundColor Green
    Start-Process -FilePath $Cloudflared `
        -ArgumentList @('tunnel', '--url', 'http://localhost:5173', '--no-autoupdate') `
        -WindowStyle Minimized

    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        try { $hostname = (Invoke-RestMethod $MetricsUrl -TimeoutSec 2).hostname } catch { $hostname = '' }
        if ($hostname) { break }
    }
}

if (-not $hostname) {
    Write-Host '[隧道] 建立失败，请打开 cloudflared 窗口看报错' -ForegroundColor Red
    exit 1
}

$url = "https://$hostname"

# ---------- 3. 地址落地：剪贴板 + 文本 + 二维码 ----------
try { $url | Set-Clipboard } catch { }

try {
    [IO.File]::WriteAllText((Join-Path $Root '手机地址.txt'), $url, (New-Object Text.UTF8Encoding($false)))
} catch { }

$QrFile = Join-Path $Root '手机地址.png'
$Python = Join-Path $Root 'backend\.venv\Scripts\python.exe'
$QrScript = Join-Path $env:TEMP 'foodai_qr.py'

$pyCode = @'
import sys
try:
    import qrcode
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(sys.argv[1])
    qr.make()
    qr.make_image().save(sys.argv[2])
except Exception:
    sys.exit(1)
'@

$qrOk = $false
if (Test-Path $Python) {
    try {
        [IO.File]::WriteAllText($QrScript, $pyCode, (New-Object Text.UTF8Encoding($false)))
        & $Python $QrScript $url $QrFile 2>$null | Out-Null
        if (Test-Path $QrFile) { $qrOk = $true }
    } catch { $qrOk = $false }
}

Write-Host ''
Write-Host '==================================================' -ForegroundColor Cyan
Write-Host '  手机浏览器打开这个地址（Safari / Chrome）：' -ForegroundColor White
Write-Host ''
Write-Host "      $url" -ForegroundColor Green
Write-Host ''
Write-Host '  ✅ 已复制到剪贴板' -ForegroundColor DarkGray
Write-Host '  ✅ 已写入 手机地址.txt' -ForegroundColor DarkGray
if ($qrOk) {
    Write-Host '  ✅ 已生成 手机地址.png（已自动打开，手机扫码即可）' -ForegroundColor DarkGray
}
Write-Host ''
Write-Host '  ⚠ 别用微信 / 夸克 / UC / QQ 浏览器' -ForegroundColor Yellow
Write-Host '    它们不给网页摄像头权限，会看到黑屏' -ForegroundColor Yellow
Write-Host ''
Write-Host '  电脑端调试：http://127.0.0.1:5173' -ForegroundColor Gray
Write-Host '  接口文档：  http://127.0.0.1:8000/docs' -ForegroundColor Gray
Write-Host '==================================================' -ForegroundColor Cyan
Write-Host '  手机若显示 Error 1033：等 10~20 秒下拉刷新即可' -ForegroundColor DarkGray
Write-Host '  （隧道刚建立时 Cloudflare 边缘节点还没生效）' -ForegroundColor DarkGray

if ($qrOk) { Start-Process $QrFile }
