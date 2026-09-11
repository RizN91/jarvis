@echo off
rem ===========================================================================
rem  Jarvis - normal launcher.  Starts the tray app with pythonw, so no
rem  console window appears.  Logs still go to
rem  %LOCALAPPDATA%\Jarvis\logs\jarvis.log
rem
rem  Prefers the local .venv created by setup.cmd; falls back to "python".
rem  Use run-console.cmd instead when something is wrong and you need output.
rem ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%~dp0"
set "PY=python"
set "PYW=pythonw"

if exist "%ROOT%.venv\Scripts\python.exe"  set "PY=%ROOT%.venv\Scripts\python.exe"
if exist "%ROOT%.venv\Scripts\pythonw.exe" set "PYW=%ROOT%.venv\Scripts\pythonw.exe"

rem ---- 1. is there a usable interpreter at all? ---------------------------
"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 goto :no_python

rem ---- 2. can the package itself be imported? -----------------------------
rem         (run from %~dp0, so the project folder is on sys.path)
"%PY%" -c "import jarvis" >nul 2>nul
if errorlevel 1 goto :no_package

rem ---- 3. is the "python -m jarvis" entry point present? --------------
"%PY%" -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('jarvis.__main__') else 7)" >nul 2>nul
if errorlevel 1 goto :no_entry

rem ---- launch -------------------------------------------------------------
start "" "%PYW%" -m jarvis %*
exit /b 0

:no_python
echo.
echo [Jarvis] ERROR: no Python interpreter found.
echo   Tried: "%PY%"
echo.
echo   Install 64-bit Python 3.11 or newer and tick "Add python.exe to PATH"
echo   during setup, then run setup.cmd once, then run.cmd again.
echo.
pause
exit /b 1

:no_package
echo.
echo [Jarvis] ERROR: the jarvis package could not be imported.
echo   Interpreter: "%PY%"
echo   Folder:      "%CD%"
echo.
echo   The interpreter exists but the app's code or its dependencies are
echo   missing (or this script was copied out of the project folder).
echo   Fix:  run setup.cmd, then run run-console.cmd to see the real error.
echo.
pause
exit /b 1

:no_entry
echo.
echo [Jarvis] ERROR: the jarvis entry point is missing.
echo   "python -m jarvis" needs jarvis\__main__.py, which was not
echo   found by the interpreter below.
echo   Interpreter: "%PY%"
echo   Folder:      "%CD%"
echo.
echo   This is a broken/incomplete checkout rather than a Python problem.
echo   Run run-console.cmd for the raw error.
echo.
pause
exit /b 1
