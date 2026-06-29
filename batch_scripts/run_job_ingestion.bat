@echo off
:: ============================================================
::  Job Ingestion Pipeline - Task Scheduler Wrapper
::  Schedule this via Task Scheduler (see register_tasks.bat)
:: ============================================================

setlocal EnableDelayedExpansion

set PROJECT_ROOT=C:\Users\ankit\AgenticApps\demo01_pipeline\prospect_platform
set PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe
set SCRIPT=%PROJECT_ROOT%\scheduler\run_ingest.py
set LOG_DIR=C:\logs\scheduler
set LOG=%LOG_DIR%\job_ingestion.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo. >> "%LOG%"
echo =============================== >> "%LOG%"
echo [%date% %time%] START job_ingestion >> "%LOG%"
echo =============================== >> "%LOG%"

cd /d "%PROJECT_ROOT%"
"%PYTHON%" "%SCRIPT%" >> "%LOG%" 2>&1

if %errorLevel% equ 0 (
    echo [%date% %time%] FINISHED OK >> "%LOG%"
) else (
    echo [%date% %time%] FAILED - exit code %errorLevel% >> "%LOG%"
)
