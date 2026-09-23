@echo off
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
py -3 -m pip install -r requirements.txt -q >nul 2>&1
py -3 run_local.py || python run_local.py
if errorlevel 1 (
  echo Python is nodig. Installeer Python en probeer opnieuw.
  pause
)
