@echo off
cd /d "%~dp0frontend"
if not exist "node_modules" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
echo ErrLens UI: http://localhost:3000  (press Ctrl+C to stop)
call npm run dev
pause
