# Cài đặt ROC lần đầu (hoặc sau khi cập nhật mã nguồn).
# Không cài Claude Code / Antigravity CLI — bạn tự cài và đăng nhập. Thư viện Python (librosa, yt-dlp) cài từ requirements.txt.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== ROC: cai dat ==" -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Can Python 3.11+ trong PATH" }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Can Node.js 20+ trong PATH" }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Write-Warning "Chua thay ffmpeg trong PATH - buoc dung video se khong chay duoc" }

if (-not (Test-Path "$Root\.venv")) { python -m venv "$Root\.venv" }
& "$Root\.venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
& "$Root\.venv\Scripts\python.exe" -m pip install -r "$Root\backend\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install that bai" }

Push-Location "$Root\frontend"
npm install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm install that bai" }
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "build giao dien that bai" }
Pop-Location

Push-Location "$Root\backend"
& "$Root\.venv\Scripts\python.exe" -m mini migrate
Pop-Location
Write-Host "Xong. Chay Mini.bat de mo ROC." -ForegroundColor Green
