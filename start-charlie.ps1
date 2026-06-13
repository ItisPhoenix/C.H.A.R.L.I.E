<#
    start-charlie.ps1 - One-shot launcher for Charlie v0.1

    Brings up the entire stack:
      1. Pre-flight checks (.env, LLM_URL, LLM_API_KEY, uv, node)
      2. Ensures Python deps (uv sync) and dashboard deps (npm install)
      3. Launches the Python daemon  (Brain, Audio, Vision, Browser, Telegram,
         Control Server :8090, tray)
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
        Write-Host "      Created a blank .env. Add LLM_URL and LLM_API_KEY to it." -ForegroundColor Gray
    }
}

# LLM_URL / LLM_API_KEY set?
$envText = if (Test-Path $envPath) { Get-Content $envPath -Raw } else { "" }
$llmUrlSet = $envText -match "(?m)^\s*LLM_URL\s*=\s*\S+"
$llmKeySet = $envText -match "(?m)^\s*LLM_API_KEY\s*=\s*\S+"
if (-not $llmUrlSet) {
    Write-Warn "LLM_URL is not set in .env."
    Write-Host "      Charlie's reasoning (the Brain) needs it. Add this line to .env:" -ForegroundColor Gray
    Write-Host "          LLM_URL=https://your-openai-compatible-endpoint/v1" -ForegroundColor White
    Write-Host "      Any OpenAI-compatible server works (LM Studio, Ollama, NIM, OpenRouter, vLLM, etc.)." -ForegroundColor Gray
}
if (-not $llmKeySet) {
    Write-Warn "LLM_API_KEY is not set. Some endpoints (NIM, OpenRouter) require it; local servers (LM Studio) do not."
}
if ($llmUrlSet) {
    Write-Ok "LLM_URL present"
    if ($llmKeySet) { Write-Ok "LLM_API_KEY present" }
}

# -----------------------------------------------------------------------------
# 2. DEPENDENCIES
# -----------------------------------------------------------------------------
Write-Step "Syncing Python dependencies (uv sync)"
uv sync
if ($LASTEXITCODE -ne 0) { Write-Err "uv sync failed. See output above."; Read-Host "Press Enter to exit"; exit 1 }
Write-Ok "Python environment ready"

# -----------------------------------------------------------------------------
# 3. KILL STALE PROCESSES
# -----------------------------------------------------------------------------
Write-Step "Checking for stale Charlie processes"
$staleProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "charlie\s+daemon" -or $_.CommandLine -match "main\.py\s+--daemon" -or $_.CommandLine -match "charlie" } |
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
uv run charlie doctor
Write-Host ""

if ($haveNode) {
    $dashDir = Join-Path $root "dashboard"
    $nodeModules = Join-Path $dashDir "node_modules"
    $prodBuild = Join-Path $dashDir ".next\BUILD_ID"
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
    if ($haveNode) {
        if (-not (Test-Path $prodBuild)) {
            Write-Step "Building dashboard (npm run build) - first run only, may take a minute"
            Push-Location $dashDir
            npm run build
            $buildExit = $LASTEXITCODE
            Pop-Location
            if ($buildExit -ne 0) {
                Write-Warn "Dashboard build failed - falling back to dev server"
                $fallbackDev = $true
            } else {
                Write-Ok "Dashboard built"
                $fallbackDev = $false
            }
        } else {
            Write-Ok "Dashboard already built"
            $fallbackDev = $false
        }
    }
}

# -----------------------------------------------------------------------------
# 5. LAUNCH
# -----------------------------------------------------------------------------
Write-Step "Launching Charlie daemon (Brain, Audio, Vision, Browser, Control Server :8090)"
Start-Process -FilePath "uv" -ArgumentList "run","charlie","daemon" `
    -WorkingDirectory $root -WindowStyle Hidden
Write-Ok "Daemon started (hidden, tray icon in system tray)"

if ($haveNode) {
    $dashScript = if ($fallbackDev) { "dev" } else { "start" }
    Write-Step "Launching dashboard UI (Next.js $dashScript on :3000)"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c","npm run $dashScript" `
        -WorkingDirectory (Join-Path $root "dashboard") -WindowStyle Normal
    Write-Ok "Dashboard starting in its own window"
    Write-Step "Waiting for the control server on :8090..."
    $ready = $false
    for ($i = 0; $i -lt 15; $i++) {
        try {
            $null = Invoke-WebRequest -Uri "http://127.0.0.1:8090/api/token" -TimeoutSec 2 -ErrorAction Stop
            $ready = $true
            break
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if ($ready) {
        Write-Ok "Control server is up"
        Write-Step "Opening browser to dashboard"
        Start-Process "http://localhost:3000/"
    } else {
        Write-Warn "Control server not reachable after 15s — the daemon may still be starting. Opening browser anyway."
        Start-Process "http://localhost:3000/"
    }
} else {
    Write-Warn "Dashboard skipped (no npm). Control Server REST/WS is still available on http://localhost:8090/"
}

Write-Host "`n==========================================================" -ForegroundColor Magenta
Write-Host "   Charlie is starting up." -ForegroundColor Green
Write-Host "   Dashboard : http://localhost:3000/" -ForegroundColor White
Write-Host "   Control   : http://localhost:8090/" -ForegroundColor White
Write-Host "   Dashboard window is open. Daemon runs in the background (tray icon). Close the windows to stop Charlie." -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Magenta
Write-Host ""
Read-Host "Press Enter to close this launcher window (Charlie keeps running)"
