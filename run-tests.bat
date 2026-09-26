@echo off
cd /d "%~dp0backend"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
echo === Automated tests ===
".venv\Scripts\python.exe" -m pytest -q
echo.
choice /M "Also run the live test against the real LLM"
if errorlevel 2 goto end
".venv\Scripts\python.exe" scripts\live_llm_test.py -v
:end
pause
