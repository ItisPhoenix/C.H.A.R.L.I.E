<#
    start-charlie.ps1 - One-shot launcher for Charlie v0.1

    Brings up the entire stack:
      1. Pre-flight checks (.env, NIM_API_KEY, uv, node)
      2. Ensures Python deps (uv sync) and dashboard deps (npm install)
      3. Launches the Python daemon  (Brain, Audio, Vision, Browser, Telegram,
         Control Server :8090, FastAPI backend :3005, tray)
      4. Launches the Next.js dashboard UI on :3000
      5. Opens the dashboard in your browser

    Run by double-clicking start-charlie.bat, or:  powershell -File start-charlie.ps1
#>

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

function Write-Step($msg)  { Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "    !!  $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "    XX  $msg" -ForegroundColor Red }

Write-Host "==========================================================" -ForegroundColor Magenta
Write-Host "   C.H.A.R.L.I.E. v0.1 - Unified Launcher" -ForegroundColor Magenta
Write-Host "==========================================================" -ForegroundColor Magenta

# -----------------------------------------------------------------------------
# 1. PRE-FLIGHT CHECKS
# -----------------------------------------------------------------------------
Write-Step "Pre-flight checks"

# uv present?
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Err "uv is not installed or not on PATH."
    Write-Host "      Install it from https://docs.astral.sh/uv/ then re-run." -ForegroundColor Gray
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Ok "uv found"

# node / npm present?
$haveNode = [bool](Get-Command npm -ErrorAction SilentlyContinue)
if (-not $haveNode) {
    Write-Warn "npm not found - the dashboard UI (port 3000) will be skipped."
    Write-Host "      Install Node.js LTS from https://nodejs.org/ to enable the dashboard." -ForegroundColor Gray
} else {
    Write-Ok "npm found"
}

# .env present?
$envPath = Join-Path $root ".env"
if (-not (Test-Path $envPath)) {
    Write-Warn ".env not found."
    $examplePath = Join-Path $root ".env.example"
    if (Test-Path $examplePath) {
        Copy-Item $examplePath $envPath
        Write-Host "      Created .env from .env.example. Edit it to add your keys." -ForegroundColor Gray
    } else {
        New-Item -ItemType File -Path $envPath | Out-Null
        Write-Host "      Created a blank .env. Add NIM_API_KEY=... to it." -ForegroundColor Gray
    }
}

# NIM_API_KEY set?
$envText = if (Test-Path $envPath) { Get-Content $envPath -Raw } else { "" }
$nimSet = $envText -match "(?m)^\s*NIM_API_KEY\s*=\s*\S+"
if (-not $nimSet) {
    Write-Warn "NIM_API_KEY is not set in .env."
    Write-Host "      Charlie's reasoning (the Brain) needs it. Add this line to .env:" -ForegroundColor Gray
    Write-Host "          NIM_API_KEY=nvapi-xxxxxxxxxxxxxxxx" -ForegroundColor White
    Write-Host "      Get a free key at https://build.nvidia.com/ (or set GEMINI_API_KEY as fallback)." -ForegroundColor Gray
    Write-Host "      Launching anyway so you can explore the dashboard - the Brain will report the missing key." -ForegroundColor Gray
} else {
    Write-Ok "NIM_API_KEY present"
}

# -----------------------------------------------------------------------------
# 2. DEPENDENCIES
# -----------------------------------------------------------------------------
Write-Step "Syncing Python dependencies (uv sync)"
uv sync
if ($LASTEXITCODE -ne 0) { Write-Err "uv sync failed. See output above."; Read-Host "Press Enter to exit"; exit 1 }
Write-Ok "Python environment ready"

if ($haveNode) {
    $dashDir = Join-Path $root "dashboard"
    $nodeModules = Join-Path $dashDir "node_modules"
    if (-not (Test-Path $nodeModules)) {
        Write-Step "Installing dashboard dependencies (npm install) - first run only, may take a few minutes"
        Push-Location $dashDir
        npm install
        $npmExit = $LASTEXITCODE
        Pop-Location
        if ($npmExit -ne 0) {
            Write-Warn "npm install failed - dashboard UI will be skipped. Daemon will still run."
            $haveNode = $false
        } else {
            Write-Ok "Dashboard dependencies installed"
        }
    } else {
        Write-Ok "Dashboard dependencies already installed"
    }
}

# -----------------------------------------------------------------------------
# 3. KILL STALE PROCESSES
# -----------------------------------------------------------------------------
Write-Step "Checking for stale Charlie processes"
$staleProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "main\.py\s+--daemon" -or $_.CommandLine -match "charlie" } |
    Select-Object ProcessId, CommandLine
if ($staleProcs) {
    foreach ($p in $staleProcs) {
        Write-Warn "Killing stale daemon PID $($p.ProcessId)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    Write-Ok "Stale processes cleaned up"
} else {
    Write-Ok "No stale processes found"
}

# -----------------------------------------------------------------------------
# 4. OPTIONAL DOCTOR SELF-CHECK
# -----------------------------------------------------------------------------
Write-Step "Running Doctor self-check"
uv run python main.py doctor
Write-Host ""

# -----------------------------------------------------------------------------
# 5. LAUNCH
# -----------------------------------------------------------------------------
Write-Step "Launching Charlie daemon (Brain, Audio, Vision, Browser, Control Server :8090, backend :3005)"
Start-Process -FilePath "uv" -ArgumentList "run","python","main.py","--daemon" `
    -WorkingDirectory $root -WindowStyle Normal
Write-Ok "Daemon starting in its own window"

if ($haveNode) {
    Write-Step "Launching dashboard UI (Next.js dev server on :3000)"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c","npm run dev" `
        -WorkingDirectory (Join-Path $root "dashboard") -WindowStyle Normal
    Write-Ok "Dashboard starting in its own window"

    Write-Step "Waiting for the dashboard to come up, then opening your browser"
    Start-Sleep -Seconds 8
    Start-Process "http://localhost:3000/"
} else {
    Write-Warn "Dashboard skipped (no npm). Control Server REST/WS is still available on http://localhost:8090/"
}

Write-Host "`n==========================================================" -ForegroundColor Magenta
Write-Host "   Charlie is starting up." -ForegroundColor Green
Write-Host "   Dashboard : http://localhost:3000/" -ForegroundColor White
Write-Host "   Control   : http://localhost:8090/" -ForegroundColor White
Write-Host "   Two windows opened (daemon + dashboard). Close them to stop Charlie." -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Magenta
Write-Host ""
Read-Host "Press Enter to close this launcher window (Charlie keeps running)"
