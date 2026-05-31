@echo off
REM ============================================================
REM  C.H.A.R.L.I.E. v0.1 - One-Click Launcher
REM
REM  Just double-click this file. It brings up the whole stack:
REM    - Python daemon (Brain, Audio/STT, TTS, Vision, Control :8090, backend)
REM    - Next.js dashboard UI on http://localhost:3000
REM    - Opens your browser to the dashboard
REM
REM  This wrapper exists so the launcher works on a double-click
REM  regardless of your PowerShell execution policy.
REM ============================================================

title C.H.A.R.L.I.E. Launcher

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-charlie.ps1"

REM If PowerShell itself failed to start, keep the window open so the
REM error is readable instead of vanishing instantly on a double-click.
if errorlevel 1 (
    echo.
    echo The launcher exited with an error. See the messages above.
    pause
)
