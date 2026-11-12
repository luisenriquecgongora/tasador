@echo off
setlocal
title Tasador engine
rem Double-click to run Tasador on this computer at http://localhost:8000
rem First run installs the engine into services\tasador-engine\.venv.
rem Settings come from a .env file next to this one (copy .env.example), if present.

set "ROOT=%~dp0"
set "ENGINE=%ROOT%services\tasador-engine"
set "VENV=%ENGINE%\.venv"
set "PY=%VENV%\Scripts\python.exe"
set "PORT=8000"
chcp 65001 >nul
set "PYTHONUTF8=1"
rem Use the Windows certificate store (works behind antivirus / corporate TLS inspection)
set "UV_NATIVE_TLS=1"

if exist "%PY%" goto :configure

echo [Tasador] First run: installing the engine into services\tasador-engine\.venv ...
where uv >nul 2>nul
if errorlevel 1 goto :setup_pip
uv venv "%VENV%" || goto :setup_failed
uv pip install --python "%PY%" -e "%ENGINE%[ocr,barcode,heic]" || goto :setup_failed
goto :configure

:setup_pip
python -m venv "%VENV%" || goto :setup_failed
"%PY%" -m pip install -e "%ENGINE%[ocr,barcode,heic]" || goto :setup_failed

:configure
if exist "%ROOT%.env" (
    echo [Tasador] Reading settings from .env
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ROOT%.env") do if not "%%B"=="" set "%%A=%%B"
)
if not defined TASADOR_DEPLOYMENT set "TASADOR_DEPLOYMENT=local"

rem No key and no model server configured: use Ollama if it is running on this computer.
if not defined ANTHROPIC_API_KEY if not defined TASADOR_VISION_BASE_URL if not defined TASADOR_VISION_PROVIDER (
    curl -s -o nul -m 2 http://localhost:11434/api/tags && (
        set "TASADOR_VISION_PROVIDER=ollama"
        echo [Tasador] Found Ollama on this computer: using it for vision
    )
)

echo [Tasador] Starting on http://localhost:%PORT%  ^(try it in the browser: http://localhost:%PORT%/docs^)
echo [Tasador] Close this window to stop it.
start "" "http://localhost:%PORT%/docs"
cd /d "%ENGINE%"
"%PY%" -m uvicorn tasador_engine.main:app_from_env --factory --host 127.0.0.1 --port %PORT%
set "RC=%errorlevel%"
if not "%RC%"=="0" pause
exit /b %RC%

:setup_failed
echo.
echo [Tasador] Setup failed. Install Python 3.11+ (and optionally uv), then run this file again.
pause
exit /b 1
