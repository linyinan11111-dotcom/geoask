@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import geoask, tkinter" >nul 2>&1
  if errorlevel 1 goto missing
  start "" ".venv\Scripts\pythonw.exe" "%~dp0geoask_gui.py"
) else (
  python -c "import geoask, tkinter" >nul 2>&1
  if errorlevel 1 goto missing
  start "" pythonw "%~dp0geoask_gui.py"
)
exit /b 0
:missing
echo Install dependencies first and ensure Python includes Tkinter:
echo python -m pip install -r requirements.txt
pause
exit /b 1
