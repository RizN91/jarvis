@echo off
REM Talk to GPT-Live and hear it back. Nothing else involved.
REM
REM   talk.cmd                talk to it
REM   talk.cmd --selftest     check your mic, speakers and API key
REM   talk.cmd --miclevel     live microphone meter (free, no API calls)
REM   talk.cmd --list         list your microphones and speakers
REM
REM Double-click it, or run it from a terminal. Must stay CRLF.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   Jarvis is not set up yet - there is no virtual environment.
  echo   Run setup.cmd first, then try again.
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" simple_voice.py %*
set RC=%ERRORLEVEL%

if "%1"=="" (
  echo.
  echo   Session ended. Press any key to close.
  pause >nul
)
exit /b %RC%
