@echo off
rem ===========================================================================
rem  Jarvis - portable release builder (PyInstaller).
rem
rem  Builds a windowed, one-folder release from installer\jarvis.spec.
rem  Output: dist\Jarvis\  (run dist\Jarvis\Jarvis.exe)
rem
rem  PyInstaller is NOT installed by this script and is NOT a runtime
rem  dependency.  If it is missing you get instructions and a non-zero exit
rem  code - this script never pretends a build succeeded.
rem ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%~dp0"
set "PY=python"
if exist "%ROOT%.venv\Scripts\python.exe" set "PY=%ROOT%.venv\Scripts\python.exe"

set "SPEC=%ROOT%installer\jarvis.spec"

echo ===========================================================================
echo  Jarvis - portable build
echo  interpreter: "%PY%"
echo  spec:        "%SPEC%"
echo ===========================================================================
echo.

if not exist "%SPEC%" goto :no_spec

echo [1/2] checking for PyInstaller
"%PY%" -c "import PyInstaller; print('      found PyInstaller ' + PyInstaller.__version__)" 2>nul
if errorlevel 1 goto :no_pyinstaller

echo [2/2] building (one folder, windowed, no console)
"%PY%" -m PyInstaller --noconfirm --clean ^
  --distpath "%ROOT%dist" ^
  --workpath "%ROOT%build" ^
  "%SPEC%"
if errorlevel 1 goto :build_failed

echo.
echo ===========================================================================
echo  Build finished.
echo    portable app: "%ROOT%dist\Jarvis\Jarvis.exe"
echo.
echo  Ship the whole "%ROOT%dist\Jarvis" folder - the exe needs the
echo  _internal folder beside it.  The end user does NOT need Python.
echo  They DO need the Microsoft Edge WebView2 runtime (present on Windows 11;
echo  see docs\INSTALL.md for the download link for Windows 10).
echo ===========================================================================
exit /b 0

:no_pyinstaller
echo.
echo   PyInstaller is NOT installed in that interpreter - nothing was built.
echo.
echo   This is deliberate.  A build-time tool must not quietly install itself
echo   into your environment, so this script stops here instead.
echo.
echo   To build the portable release, install PyInstaller and re-run package.cmd:
echo.
echo       "%PY%" -m pip install pyinstaller
echo.
echo   (This only affects the interpreter above.  For the local .venv, run
echo    setup.cmd first - package.cmd then picks .venv automatically.)
echo.
exit /b 1

:no_spec
echo   FAILED - the PyInstaller spec file is missing:
echo     "%SPEC%"
echo   Restore installer\jarvis.spec before building.
exit /b 1

:build_failed
echo.
echo   FAILED - PyInstaller returned a non-zero exit code.
echo   Read the messages above; the build output is left under:
echo     "%ROOT%build"  and  "%ROOT%dist"
exit /b 1
