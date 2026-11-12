@echo off
setlocal
title Tasador desktop
rem Double-click to open the Tasador desktop app: a small window with the engine status,
rem the port, and the settings page. The engine runs hidden; the tray icon quits it.
rem First run installs the engine (via Tasador.bat) and builds the app with Rust (several minutes).

set "ROOT=%~dp0"
set "APP=%ROOT%apps\desktop\src-tauri"
set "EXE=%APP%\target\release\tasador-desktop.exe"

call "%ROOT%Tasador.bat" --install-only || goto :failed

where cargo >nul 2>nul
if errorlevel 1 (
    echo [Tasador] Building the desktop app needs Rust: install it from https://rustup.rs, then run this file again.
    pause
    exit /b 1
)
if not exist "%EXE%" echo [Tasador] First run: building the desktop app (several minutes) ...
pushd "%APP%"
cargo build --release
set "RC=%errorlevel%"
popd
if not "%RC%"=="0" goto :failed

start "" "%EXE%"
exit /b 0

:failed
echo.
echo [Tasador] Could not install or build the desktop app. The messages above say why.
pause
exit /b 1
