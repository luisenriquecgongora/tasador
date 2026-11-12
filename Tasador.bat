@echo off
setlocal
title Tasador
rem One click to run Tasador on this computer at http://localhost:8000
rem   1. installs (or repairs) the engine in services\tasador-engine\.venv
rem   2. reads settings from .env next to this file, if present (copy .env.example)
rem   3. recognition: uses Ollama if it is running, with an installed model that reads images
rem   4. links and prices: starts SearXNG in Docker if Docker Desktop is running
rem   5. starts the engine and opens the settings page, where you can change all of this
rem Anything set in .env wins over these automatic choices.
rem TASADOR_LAUNCHER_DRY_RUN=1 prints the choices and stops before starting the engine.

set "ROOT=%~dp0"
set "ENGINE=%ROOT%services\tasador-engine"
set "VENV=%ENGINE%\.venv"
set "PY=%VENV%\Scripts\python.exe"
set "PORT=8000"
set "PYTHONPATH=%ENGINE%\src"
chcp 65001 >nul
set "PYTHONUTF8=1"
rem Use the Windows certificate store for installs (works behind antivirus / company proxies)
set "UV_NATIVE_TLS=1"

rem ---- 1. Install or repair -----------------------------------------------------------
if not exist "%PY%" goto :install
"%PY%" -c "import tasador_engine.main" >nul 2>nul && goto :configure
echo [Tasador] The installation is incomplete: updating it ...
goto :install_packages

:install
echo [Tasador] First run: installing the engine (a few minutes) ...
where uv >nul 2>nul
if errorlevel 1 (
    python -m venv "%VENV%" || goto :setup_failed
) else (
    uv venv "%VENV%" || goto :setup_failed
)

:install_packages
where uv >nul 2>nul
if errorlevel 1 (
    "%PY%" -m pip install -e "%ENGINE%[ocr,barcode,heic]" || goto :setup_failed
) else (
    uv pip install --python "%PY%" -e "%ENGINE%[ocr,barcode,heic]" || goto :setup_failed
)

rem ---- 2. Settings ---------------------------------------------------------------------
:configure
if exist "%ROOT%.env" (
    echo [Tasador] Reading settings from .env
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ROOT%.env") do if not "%%B"=="" set "%%A=%%B"
)
if not defined TASADOR_DEPLOYMENT set "TASADOR_DEPLOYMENT=local"

rem ---- 3. Recognition ------------------------------------------------------------------
if defined TASADOR_VISION_PROVIDER goto :vision_model
if defined TASADOR_VISION_BASE_URL goto :vision_other
if defined ANTHROPIC_API_KEY goto :vision_other
curl -s -o nul -m 2 http://localhost:11434/api/tags || goto :no_ollama
set "TASADOR_VISION_PROVIDER=ollama"

:vision_model
if /i not "%TASADOR_VISION_PROVIDER%"=="ollama" goto :vision_other
if defined TASADOR_VISION_MODEL goto :vision_found
for /f "usebackq delims=" %%M in (`"%PY%" -m tasador_engine.ollama_models`) do set "TASADOR_VISION_MODEL=%%M"
if defined TASADOR_VISION_MODEL goto :vision_found
echo [Tasador] Recognition: Ollama is running but has no model that reads images.
echo [Tasador]   Install one, for example:  ollama pull qwen3.5:2b   (then start this file again)
set "TASADOR_VISION_PROVIDER=none"
goto :search

:vision_found
echo [Tasador] Recognition: Ollama on this computer, model %TASADOR_VISION_MODEL%
goto :search

:vision_other
echo [Tasador] Recognition: as set in .env
goto :search

:no_ollama
echo [Tasador] Recognition: off (start Ollama, or set a provider in .env). Barcodes and label text still work.

rem ---- 4. Links and prices -------------------------------------------------------------
:search
if defined TASADOR_SEARCH_PROVIDER goto :search_other
if defined TASADOR_SEARXNG_URL goto :search_other
if defined TASADOR_SEARCH_API_KEY goto :search_other
if defined ANTHROPIC_API_KEY goto :search_other
rem Already running (for example from an earlier start)? Then Docker is not touched at all.
curl -s -o nul -m 3 http://localhost:8888/healthz && goto :searxng_ready
docker info >nul 2>nul || goto :no_docker
echo [Tasador] Links and prices: starting SearXNG in Docker (can take a minute) ...
pushd "%ROOT%"
docker compose --profile local up -d searxng >nul 2>nul
set "RC=%errorlevel%"
popd
if not "%RC%"=="0" goto :no_docker

:searxng_ready
set "TASADOR_SEARXNG_URL=http://localhost:8888"
echo [Tasador] Links and prices: SearXNG on http://localhost:8888 (it keeps running in Docker after you close this window)
goto :start

:search_other
echo [Tasador] Links and prices: as set in .env
goto :start

:no_docker
echo [Tasador] Links and prices: off (start Docker Desktop for local search, or set a provider in .env)

rem ---- 5. Start ------------------------------------------------------------------------
:start
rem The settings page at http://localhost:%PORT%/ (pick providers and models, try a photo)
rem saves its changes to the .env next to this file.
set "TASADOR_SETTINGS_UI=1"
set "TASADOR_ENV_FILE=%ROOT%.env"
if defined TASADOR_LAUNCHER_DRY_RUN goto :dry_run
echo [Tasador] Starting on http://localhost:%PORT%
echo [Tasador]   settings and test page: http://localhost:%PORT%/   API docs: http://localhost:%PORT%/docs
echo [Tasador] Close this window to stop it.
rem Open the settings page a few seconds later, once the engine is listening.
start "" /b cmd /c "timeout /t 6 /nobreak >nul & start http://localhost:%PORT%/"
"%PY%" -m uvicorn tasador_engine.main:app_from_env --factory --app-dir "%ENGINE%\src" --host 127.0.0.1 --port %PORT%
set "RC=%errorlevel%"
if not "%RC%"=="0" pause
exit /b %RC%

:dry_run
echo [Tasador] Dry run: vision=%TASADOR_VISION_PROVIDER% model=%TASADOR_VISION_MODEL% searxng=%TASADOR_SEARXNG_URL% env_file=%TASADOR_ENV_FILE%
exit /b 0

:setup_failed
echo.
echo [Tasador] Setup failed. Install Python 3.11+ (and optionally uv), then run this file again.
pause
exit /b 1
