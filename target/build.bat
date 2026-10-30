@echo off
rem Build the notes app into a Windows .exe (PyInstaller folder mode).
rem Double-click this file to build. Output: target\dist\ folder.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH. Install Python 3 first.
  pause
  exit /b 1
)

echo Building notes app with PyInstaller...
python "%~dp0build.py"
if errorlevel 1 (
  echo.
  echo [FAILED] Build failed. See messages above.
  pause
  exit /b 1
)

echo.
echo [OK] Build finished. Output: target\dist\
pause
