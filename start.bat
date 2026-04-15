@echo off
title OpenMTOps
cd /d "%~dp0"

echo.
echo  ==========================================
echo   OpenMTOps — Starting up
echo  ==========================================
echo.

:: ── Free port 5000 if a stale Python process is holding it ─────────────────
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$conn = netstat -ano | Select-String '127\.0\.0\.1:5000\s';" ^
  "if ($conn) {" ^
  "  $id = ($conn[0].ToString().Trim() -split '\s+')[-1];" ^
  "  $proc = Get-Process -Id $id -ErrorAction SilentlyContinue;" ^
  "  if ($proc -and $proc.Name -like 'python*') {" ^
  "    Write-Host \"  [!] Stale Python process on port 5000 (PID $id) — killing it...\" -ForegroundColor Yellow;" ^
  "    Stop-Process -Id $id -Force;" ^
  "    Start-Sleep -Milliseconds 800;" ^
  "    Write-Host '  [+] Port 5000 is now free.' -ForegroundColor Green" ^
  "  } else {" ^
  "    Write-Host \"  [!] Port 5000 held by non-Python process (PID $id) — skipping.\" -ForegroundColor Red" ^
  "  }" ^
  "} else {" ^
  "  Write-Host '  [+] Port 5000 is free.' -ForegroundColor Green" ^
  "}"

echo.

:: ── Virtualenv: validate, recreate if broken, create if missing ─────────────

:: Case 1: venv exists but pyvenv.cfg is missing (broken venv)
if exist "venv" (
    if not exist "venv\pyvenv.cfg" (
        echo  [!] venv is broken — pyvenv.cfg missing. Recreating...
        rmdir /s /q venv
        python -m venv venv
        if errorlevel 1 (
            echo  [!] Failed to create venv. Is Python installed and on PATH?
            pause >nul
            exit /b 1
        )
        echo  [+] venv recreated successfully.
        echo  [!] Installing dependencies...
        call venv\Scripts\activate.bat
        pip install git+https://github.com/dhan-oss/DhanHQ-py.git --quiet
        pip install -r requirements.txt --quiet
        echo  [+] Dependencies installed.
        goto launch
    )
)

:: Case 2: venv does not exist at all
if not exist "venv" (
    echo  [!] No venv found — creating one...
    python -m venv venv
    if errorlevel 1 (
        echo  [!] Failed to create venv. Is Python installed and on PATH?
        pause >nul
        exit /b 1
    )
    echo  [+] venv created.
    echo  [!] Installing dependencies...
    call venv\Scripts\activate.bat
    pip install git+https://github.com/dhan-oss/DhanHQ-py.git --quiet
    pip install -r requirements.txt --quiet
    echo  [+] Dependencies installed.
    goto launch
)

:: Case 3: venv is valid — just activate
echo  [+] Activating virtualenv...
call venv\Scripts\activate.bat

:launch
echo  [+] Launching app...
echo.

:: ── Start the app ────────────────────────────────────────────────────────────
python app.py

:: ── Keep window open on crash so the user can read the error ────────────────
echo.
echo  [!] App exited. Press any key to close.
pause >nul
