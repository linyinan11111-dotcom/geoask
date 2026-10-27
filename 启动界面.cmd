@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "GEOASK_PYTHON="
set "GEOASK_PYTHONW="
if exist ".venv\Scripts\python.exe" (
  set "GEOASK_PYTHON=%~dp0.venv\Scripts\python.exe"
  set "GEOASK_PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
) else (
  for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "GEOASK_PYTHON=%%P"
)
if not defined GEOASK_PYTHON goto missing
if not defined GEOASK_PYTHONW for %%P in ("%GEOASK_PYTHON%") do set "GEOASK_PYTHONW=%%~dpPpythonw.exe"
if not exist "%GEOASK_PYTHONW%" goto missing
"%GEOASK_PYTHON%" -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,14), 'Python 3.11-3.13 required'; import geoask_web" >nul 2>&1
if errorlevel 1 goto missing
start "" "%GEOASK_PYTHONW%" "%~dp0geoask_web.py"
if errorlevel 1 goto failed
exit /b 0
:missing
echo Python 3.11-3.13 and pythonw.exe are required.
echo Install or upgrade from this directory:
echo python -m pip install -r requirements.txt -e .
pause
exit /b 1
:failed
echo Could not launch geoask. Try: "%GEOASK_PYTHON%" geoask_web.py
pause
exit /b 1
