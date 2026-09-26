@echo off
cd /d "%~dp0backend"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
echo ErrLens API: http://localhost:8000  (press Ctrl+C to stop)
".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000
pause
