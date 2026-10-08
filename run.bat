@echo off
REM Start Exoplanet Hunter (Windows). First run creates the venv.
cd /d "%~dp0"
if not exist .venv-win (
  python -m venv .venv-win && .venv-win\Scripts\pip install -r requirements.txt
)
.venv-win\Scripts\python server.py
