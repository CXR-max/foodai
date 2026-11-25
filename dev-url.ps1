# Show the LAN URLs a phone can use to reach the dev server (port 5173).
$ErrorActionPreference = 'SilentlyContinue'

Write-Host ''
Write-Host '  ================================================'
Write-Host '   Mobile access URLs   (dev server: 5173)'
Write-Host '  ================================================'
Write-Host ''

$rows = @()
foreach ($addr in (Get-NetIPAddress -AddressFamily IPv4)) {
    if ($addr.IPAddress -like '127.*') { continue }
    if ($addr.IPAddress -like '169.254.*') { continue }
    $ad = Get-NetAdapter -InterfaceIndex $addr.InterfaceIndex
    if (-not $ad) { continue }
    if ($ad.Status -ne 'Up') { continue }
    if ($ad.Name -match 'VMware|VirtualBox|Loopback|vEthernet|Bluetooth') { continue }
    $rows += [PSCustomObject]@{ Adapter = $ad.Name; IP = $addr.IPAddress }
}

if ($rows.Count -eq 0) {
    Write-Host '  No LAN address found. Check the network connection.'
} else {
    foreach ($r in $rows) {
        Write-Host ('  [' + $r.Adapter + ']')
        Write-Host ('      http://' + $r.IP + ':5173')
        Write-Host ''
    }
    Write-Host '  Pick the adapter that is on the same WiFi as your phone.'
}

$listening = @(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 5173, 8000 })
Write-Host ''
if ($listening.Count -ge 2) {
    Write-Host '  [OK] 5173 and 8000 are both listening.'
} elseif ($listening.Count -eq 0) {
    Write-Host '  [!!] Nothing is listening. Start backend and frontend first.'
} else {
    Write-Host ('  [!!] Only these ports are listening: ' + (($listening | ForEach-Object { $_.LocalPort }) -join ', '))
}
Write-Host ''
