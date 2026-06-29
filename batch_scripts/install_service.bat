@echo off
:: ============================================================
::  ProspectAPI - Windows Service Installer
::  Run this once as Administrator to register & start the service.
::  Re-run to update config after any path/env changes.
:: ============================================================

setlocal EnableDelayedExpansion

:: ── CONFIG ──────────────────────────────────────────────────
set PROJECT_ROOT=C:\Users\ankit\AgenticApps\demo01_pipeline\prospect_platform
set PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe
set LOG_DIR=C:\logs\prospectapi
set SERVICE_NAME=ProspectAPI
set PORT=8001
:: ────────────────────────────────────────────────────────────

echo.
echo ============================================================
echo  ProspectAPI Service Installer
echo ============================================================
echo  Project root : %PROJECT_ROOT%
echo  Python       : %PYTHON%
echo  Log dir      : %LOG_DIR%
echo  Port         : %PORT%
echo ============================================================
echo.

:: Verify running as Administrator
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Please right-click and run as Administrator.
    pause
    exit /b 1
)

:: Verify NSSM is available
where nssm >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] nssm.exe not found in PATH.
    echo         Download from https://nssm.cc/download
    echo         and copy nssm.exe to C:\Windows\System32
    pause
    exit /b 1
)

:: Verify python exists
if not exist "%PYTHON%" (
    echo [ERROR] Python not found at: %PYTHON%
    echo         Check PROJECT_ROOT and venv path at top of this file.
    pause
    exit /b 1
)

:: Create log directory
if not exist "%LOG_DIR%" (
    mkdir "%LOG_DIR%"
    echo [OK] Created log directory: %LOG_DIR%
)

:: Stop & remove existing service if present (clean reinstall)
sc query %SERVICE_NAME% >nul 2>&1
if %errorLevel% equ 0 (
    echo [INFO] Existing service found. Stopping and removing...
    nssm stop %SERVICE_NAME% >nul 2>&1
    timeout /t 3 /nobreak >nul
    nssm remove %SERVICE_NAME% confirm >nul 2>&1
    echo [OK] Old service removed.
)

echo.
echo [INFO] Installing service...

:: Install
nssm install %SERVICE_NAME% "%PYTHON%"

:: Command-line arguments (no --reload in production)
nssm set %SERVICE_NAME% AppParameters -m uvicorn app.main:app --app-dir app-ui/backend --host 0.0.0.0 --port %PORT%

:: Working directory = project root (where root .env lives)
nssm set %SERVICE_NAME% AppDirectory "%PROJECT_ROOT%"

:: Display name and description (visible in services.msc)
nssm set %SERVICE_NAME% DisplayName "Prospect Platform API"
nssm set %SERVICE_NAME% Description "FastAPI/Uvicorn backend for the Prospect Platform UI. Port %PORT%."

:: Startup type: Delayed Automatic (waits for network stack to be ready)
nssm set %SERVICE_NAME% Start SERVICE_DELAYED_AUTO_START

:: Logging — rotate at 5 MB
nssm set %SERVICE_NAME% AppStdout "%LOG_DIR%\service.log"
nssm set %SERVICE_NAME% AppStderr "%LOG_DIR%\service.log"
nssm set %SERVICE_NAME% AppRotateFiles 1
nssm set %SERVICE_NAME% AppRotateBytes 5242880
nssm set %SERVICE_NAME% AppRotateOnline 1

:: Restart policy: restart 3 seconds after any crash, up to 3 times
:: After 3 failures in a day, wait 60 seconds before next attempt
nssm set %SERVICE_NAME% AppRestartDelay 3000
nssm set %SERVICE_NAME% AppThrottle 60000

:: Ensure the process group is killed cleanly on stop
nssm set %SERVICE_NAME% AppKillProcessTree 1
nssm set %SERVICE_NAME% AppStopMethodSkip 0

echo [OK] Service configured.
echo.
echo [INFO] Starting service...
nssm start %SERVICE_NAME%
timeout /t 4 /nobreak >nul

:: Verify
sc query %SERVICE_NAME% | find "RUNNING" >nul
if %errorLevel% equ 0 (
    echo.
    echo ============================================================
    echo  [SUCCESS] %SERVICE_NAME% is RUNNING on port %PORT%
    echo  Logs : %LOG_DIR%\service.log
    echo  Manage: services.msc  or  nssm stop/start %SERVICE_NAME%
    echo ============================================================
) else (
    echo.
    echo [WARNING] Service may not have started yet. Check:
    echo   nssm status %SERVICE_NAME%
    echo   type "%LOG_DIR%\service.log"
)

echo.
pause
