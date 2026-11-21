# Khởi động backend + worker nền và mở giao diện. Chạy lại khi đang chạy: chỉ mở trình duyệt.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = "$Root\.venv\Scripts\python.exe"
$Data = if ($env:MINI_DATA_DIR) { $env:MINI_DATA_DIR } else { "$Root\data" }
$Run = "$Data\run"
$Logs = "$Data\logs"
$Port = if ($env:MINI_PORT) { $env:MINI_PORT } else { 8765 }
New-Item -ItemType Directory -Force $Run, $Logs | Out-Null

if (-not (Test-Path $Py) -or -not (Test-Path "$Root\frontend\dist\index.html")) {
    Write-Host "Chua cai dat - chay setup truoc..." -ForegroundColor Yellow
    & "$PSScriptRoot\setup-mini.ps1"
}

function Test-Alive($name) {
    $f = "$Run\$name.pid"
    if (-not (Test-Path $f)) { return $false }
    $procId = [int](Get-Content $f)
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    return ($null -ne $p -and $p.ProcessName -like "python*")
}

function Start-Part($name, $cmd) {
    if (Test-Alive $name) { Write-Host "$name dang chay"; return }
    Remove-Item "$Data\worker.stop" -ErrorAction SilentlyContinue
    $p = Start-Process -FilePath $Py -ArgumentList "-m", "mini", $cmd -WorkingDirectory "$Root\backend" `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput "$Logs\$name.out.log" -RedirectStandardError "$Logs\$name.err.log"
    Set-Content -Path "$Run\$name.pid" -Value $p.Id
    Write-Host "Da khoi dong $name (pid $($p.Id))"
}

Start-Part "server" "serve"
Start-Part "worker" "worker"

$url = "http://127.0.0.1:$Port/"
for ($i = 0; $i -lt 40; $i++) {
    try { Invoke-WebRequest -UseBasicParsing "$url`api/overview" -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Milliseconds 500 }
}
Start-Process $url
Write-Host "ROC dang chay tai $url  (dong cua so nay khong lam dung ROC; dung Stop-Mini.bat de dung)" -ForegroundColor Green
