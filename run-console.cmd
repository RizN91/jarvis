@echo off
rem ===========================================================================
rem  Jarvis - troubleshooting launcher.  Same as run.cmd but uses
rem  python.exe and stays in the foreground, so every log line and traceback
rem  is visible in this window.  The window closes when the app exits.
rem
rem  Prefers the local .venv created by setup.cmd; falls back to "python".
rem  The rotating log file is still written to
rem  %LOCALAPPDATA%\Jarvis\logs\jarvis.log
rem ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%~dp0"
set "PY=python"

if exist "%ROOT%.venv\Scripts\python.exe" set "PY=%ROOT%.venv\Scripts\python.exe"

"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 goto :no_python

"%PY%" -c "import jarvis" >nul 2>nul
if errorlevel 1 goto :no_package

"%PY%" -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('jarvis.__main__') else 7)" >nul 2>nul
if errorlevel 1 goto :no_entry

echo [Jarvis] interpreter: "%PY%"
echo [Jarvis] folder:      "%CD%"
echo [Jarvis] starting (console mode, press Ctrl+C to stop)...
echo.

"%PY%" -m jarvis %*
set "RC=%ERRORLEVEL%"

echo.
echo [Jarvis] the app exited with code %RC%
if not "%RC%"=="0" echo [Jarvis] see %LOCALAPPDATA%\Jarvis\logs\jarvis.log for the log trail
echo.
pause
exit /b %RC%

:no_python
echo.
echo [Jarvis] ERROR: no Python interpreter found.  Tried: "%PY%"
echo   Install 64-bit Python 3.11 or newer, then run setup.cmd.
echo.
pause
exit /b 1

:no_package
echo.
echo [Jarvis] ERROR: the jarvis package could not be imported.
echo   Interpreter: "%PY%"
echo   Folder:      "%CD%"
echo   Run setup.cmd (it installs requirements.lock.txt into .venv).
echo.
pause
exit /b 1

:no_entry
echo.
echo [Jarvis] ERROR: the jarvis entry point is missing.
echo   "python -m jarvis" needs jarvis\__main__.py.
echo   Interpreter: "%PY%"
echo   Folder:      "%CD%"
echo.
pause
exit /b 1
