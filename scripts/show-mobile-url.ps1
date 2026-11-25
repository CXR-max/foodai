# 查看当前手机端地址：打印 + 复制到剪贴板 + 重新生成二维码
# 用法：VS Code → 运行任务 → 「★ 查看手机地址」

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$hostname = ''
try { $hostname = (Invoke-RestMethod 'http://127.0.0.1:20241/quicktunnel' -TimeoutSec 3).hostname } catch { $hostname = '' }

if (-not $hostname) {
    Write-Host '[隧道] 没有在运行' -ForegroundColor Yellow
    Write-Host '        先运行任务「★ 一键启动（网页端 + 手机端）」' -ForegroundColor DarkGray
    exit 1
}

$url = "https://$hostname"
try { $url | Set-Clipboard } catch { }

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
Write-Host '  当前手机端地址：' -ForegroundColor White
Write-Host "      $url" -ForegroundColor Green
Write-Host ''
Write-Host '  已复制到剪贴板' -ForegroundColor DarkGray
if ($qrOk) {
    Write-Host '  已重新生成 手机地址.png' -ForegroundColor DarkGray
    Start-Process $QrFile
}
Write-Host ''
