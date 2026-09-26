@echo off
setlocal
echo === ErrLens setup ===
echo.

rem Find Python (python or the py launcher)
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (where py >nul 2>nul && set "PY=py -3")
if not defined PY (
  echo [ERROR] Python not found. Install Python 3.10+ from https://www.python.org/downloads/
  echo         and tick "Add python.exe to PATH" during installation.
  pause
  exit /b 1
)

cd /d "%~dp0backend"
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating the virtual environment: backend\.venv
  %PY% -m venv .venv || (echo [ERROR] Could not create the virtual environment & pause & exit /b 1)
) else (
  echo [1/3] Virtual environment already exists: backend\.venv
)

echo [2/3] Installing Python packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt || (echo [ERROR] Package installation failed & pause & exit /b 1)

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo       Created backend\.env from .env.example. Add your API keys to it.
)

echo [3/3] Installing frontend packages...
where npm >nul 2>nul
if errorlevel 1 (
  echo [WARNING] Node.js not found, skipping the frontend. Install the LTS version from https://nodejs.org
) else (
  cd /d "%~dp0frontend"
  call npm install || (echo [ERROR] npm install failed & pause & exit /b 1)
)

echo.
echo Setup complete.
echo   Tests    : run-tests.bat
echo   Backend  : start-backend.bat
echo   Frontend : start-frontend.bat  (then open http://localhost:3000)
pause
