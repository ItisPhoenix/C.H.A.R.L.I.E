@echo off
title C.H.A.R.L.I.E.
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo [ERROR] .venv not found. Run: uv sync
    pause
    exit /b 1
)

if not exist ".env" (
    echo [WARN] .env missing - copy .env.example to .env and fill in your keys
)

echo.
echo  ==========================================
echo   C.H.A.R.L.I.E.
echo   Dashboard: http://localhost:3000
echo  ==========================================
echo.

if exist "dashboard\package.json" (
    echo [INFO] Starting Next.js Dashboard in the background...
    start "Next.js Dashboard" /min cmd /c "cd dashboard && npm run dev"
)

python charlie-daemon.py

if errorlevel 1 (
    echo.
    echo [ERROR] Charlie exited with an error.
    pause
)
