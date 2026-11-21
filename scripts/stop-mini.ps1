# Dừng ROC gọn gàng: worker làm nốt/huỷ việc đang chạy và lưu dấu mốc, rồi dừng server.
$Root = Split-Path -Parent $PSScriptRoot
$Data = if ($env:MINI_DATA_DIR) { $env:MINI_DATA_DIR } else { "$Root\data" }
$Run = "$Data\run"

function Get-Part($name) {
    $f = "$Run\$name.pid"
    if (-not (Test-Path $f)) { return $null }
    $p = Get-Process -Id ([int](Get-Content $f)) -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -like "python*") { return $p }
    return $null
}

$w = Get-Part "worker"
if ($w) {
    Write-Host "Yeu cau worker dung (toi da 60s)..."
    Set-Content "$Data\worker.stop" "stop"
    if (-not $w.WaitForExit(60000)) {
        Write-Warning "Worker chua dung kip - buoc dung (lan khoi dong sau se doi chieu va tiep tuc)"
        taskkill /PID $w.Id /T /F | Out-Null
    }
}
$s = Get-Part "server"
if ($s) { taskkill /PID $s.Id /T /F | Out-Null }
Remove-Item "$Run\*.pid" -ErrorAction SilentlyContinue
Write-Host "ROC da dung." -ForegroundColor Green
