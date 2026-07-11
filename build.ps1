# build.ps1 — Full build: Backend (PyInstaller) → Frontend (Vite) → Installer (electron-builder)
$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "`n=== SoundCloud PlayBot — Full Build ===" -ForegroundColor Cyan
Write-Host "Root: $ROOT`n"

# ── Step 1: Build backend with PyInstaller ────────────────────────────────────
Write-Host "[1/3] Building backend..." -ForegroundColor Yellow

Push-Location "$ROOT\backend"

# Clean previous build
if (Test-Path "dist\server") { Remove-Item -Recurse -Force "dist\server" }
if (Test-Path "build\server") { Remove-Item -Recurse -Force "build\server" }

Write-Host "  Running PyInstaller..."
python -m PyInstaller server.spec --noconfirm --clean 2>&1 | ForEach-Object {
    if ($_ -match "ERROR|FATAL|error:|Cannot") { Write-Host "  $_" -ForegroundColor Red }
    elseif ($_ -match "WARNING") { Write-Host "  $_" -ForegroundColor DarkYellow }
    else { Write-Host "  $_" -ForegroundColor DarkGray }
}

if (-not (Test-Path "dist\server\server.exe")) {
    Write-Host "`n  FAILED: dist\server\server.exe not found!" -ForegroundColor Red
    Pop-Location
    exit 1
}

$backendSize = (Get-ChildItem -Recurse "dist\server" | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host "  Backend built: dist\server\server.exe ($([math]::Round($backendSize, 1)) MB)" -ForegroundColor Green

Pop-Location

# ── Step 2: Build frontend with Vite ──────────────────────────────────────────
Write-Host "`n[2/3] Building frontend..." -ForegroundColor Yellow

Push-Location "$ROOT\frontend"

# Ensure node_modules
if (-not (Test-Path "node_modules")) {
    Write-Host "  Installing npm dependencies..."
    npm install 2>&1 | Write-Host -ForegroundColor DarkGray
}

Write-Host "  Running vite build..."
npx vite build 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

if (-not (Test-Path "dist\index.html")) {
    Write-Host "`n  FAILED: dist\index.html not found!" -ForegroundColor Red
    Pop-Location
    exit 1
}

Write-Host "  Frontend built: dist\" -ForegroundColor Green

# ── Step 3: Package with electron-builder ─────────────────────────────────────
Write-Host "`n[3/3] Packaging installer..." -ForegroundColor Yellow

Write-Host "  Running electron-builder..."
npx electron-builder --win --x64 2>&1 | ForEach-Object {
    if ($_ -match "error|Error") { Write-Host "  $_" -ForegroundColor Red }
    else { Write-Host "  $_" -ForegroundColor DarkGray }
}

Pop-Location

# ── Done ──────────────────────────────────────────────────────────────────────
$installer = Get-ChildItem "$ROOT\release\*.exe" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($installer) {
    $instSize = $installer.Length / 1MB
    Write-Host "`n=== BUILD COMPLETE ===" -ForegroundColor Green
    Write-Host "Installer: $($installer.FullName)" -ForegroundColor Green
    Write-Host "Size: $([math]::Round($instSize, 1)) MB" -ForegroundColor Green
} else {
    Write-Host "`n=== BUILD FAILED — no installer found in release\ ===" -ForegroundColor Red
    exit 1
}
