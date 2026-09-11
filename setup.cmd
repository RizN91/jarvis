@echo off
rem ===========================================================================
rem  Jarvis - one-time (and safely re-runnable) setup.
rem
rem  Creates a PER-USER virtual environment at .venv next to this script and
rem  installs the exact pinned versions from requirements.lock.txt into it.
rem
rem  Guarantees:
rem    * no administrator rights required (never elevates)
rem    * the GLOBAL Python installation is never modified
rem    * no PowerShell execution policy is touched
rem    * no Defender / antivirus setting is touched
rem    * idempotent: running it twice is safe and does nothing surprising
rem ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"

echo ===========================================================================
echo  Jarvis setup
echo  project folder: %ROOT%
echo  virtual env:    %VENV%
echo ===========================================================================
echo.

rem ---- 0. find a suitable 64-bit CPython 3.11+ ------------------------------
rem  The lockfile was validated on CPython 3.11; an exact 3.11 is preferred,
rem  but any 64-bit 3.11 or newer is accepted (with a printed note otherwise).
if exist "%VENV%\Scripts\python.exe" goto :venv_exists
set "BASE="
where py >nul 2>nul
if errorlevel 1 goto :try_python
py -3.11 -c "import sys; sys.exit(0 if sys.version_info[:2] == (3,11) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if not errorlevel 1 set "BASE=py -3.11"
if defined BASE goto :have_base
py -3 -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if not errorlevel 1 set "BASE=py -3"
if defined BASE goto :have_base
:try_python
where python >nul 2>nul
if errorlevel 1 goto :no_python
python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if not errorlevel 1 set "BASE=python"
if not defined BASE goto :no_python

:have_base
echo [1/4] base interpreter: %BASE%

rem ---- 1. create the venv once -------------------------------------------
if exist "%VENV%\Scripts\python.exe" goto :venv_exists

echo [2/4] creating the virtual environment (this takes a few seconds)
%BASE% -m venv "%VENV%"
if errorlevel 1 goto :venv_failed
echo       created: "%VENV%"
goto :have_venv

:venv_exists
echo [2/4] virtual environment already exists - reusing it (nothing was wiped)

:have_venv
set "VPY=%VENV%\Scripts\python.exe"
if not exist "%VPY%" goto :venv_failed
"%VPY%" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if errorlevel 1 goto :old_python

rem ---- 2. refresh pip inside the venv only --------------------------------
echo [3/4] upgrading pip inside the venv (the global Python is untouched)
"%VPY%" -m pip install --upgrade pip
if errorlevel 1 echo       warning: pip upgrade failed (offline?) - continuing with the installed pip

rem ---- 3. install the pinned dependency set -------------------------------
echo [4/4] installing dependencies from requirements.lock.txt
"%VPY%" -m pip install -r "%ROOT%requirements.lock.txt"
if errorlevel 1 goto :pip_failed

rem ---- 4. smoke check: does the package actually import? ------------------
echo       smoke check: python -c "import jarvis"
"%VPY%" -c "import jarvis" >nul 2>nul
if errorlevel 1 goto :import_failed
"%VPY%" -c "import jarvis, sounddevice, webview, sherpa_onnx; print('      jarvis %s imports OK' % jarvis.__version__)"
if errorlevel 1 goto :import_failed

echo.
echo ===========================================================================
echo  Setup finished.
echo    * virtual environment: "%VENV%"
echo    * interpreter:         "%VPY%"
echo    * your GLOBAL Python was NOT modified.
echo.
echo  Launch the app with:  run.cmd
echo  If it does not start: run-console.cmd  and read the output.
echo ===========================================================================
exit /b 0

:no_python
echo [1/4] FAILED - no suitable interpreter found.
echo.
echo   No 64-bit CPython 3.11 or newer was found on PATH ("py" and "python").
echo   Install 64-bit Python 3.11 from https://www.python.org/downloads/ and
echo   tick "Add python.exe to PATH" in the installer, then run setup.cmd again.
exit /b 1

:old_python
echo       FAILED - that interpreter does not match this lockfile.
echo.
echo   This dependency lock requires 64-bit CPython 3.11 or newer, and the
echo   existing .venv is older than that. Delete the .venv folder and re-run.
exit /b 1

:venv_failed
echo       FAILED - the virtual environment could not be created at:
echo         "%VENV%"
echo.
echo   Check the folder is writable and that "python -m venv" works.
echo   Nothing else was changed.
exit /b 1

:pip_failed
echo.
echo   FAILED - pip could not install the pinned dependencies.
echo   Common causes: no internet access, or a proxy/firewall blocking PyPI.
echo   The virtual environment at "%VENV%" was left in place; fix the cause
echo   and simply re-run setup.cmd (it is safe to run repeatedly).
exit /b 1

:import_failed
echo.
echo   FAILED - the dependencies installed but "import jarvis" still fails.
echo   Run run-console.cmd to see the real Python traceback.
exit /b 1
