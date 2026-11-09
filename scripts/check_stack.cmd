@echo off
setlocal
cd /d "%~dp0.."
set "FAILED=0"
echo Query Pilot stack status
docker compose -f docker-compose.mysql.yml ps
if errorlevel 1 set "FAILED=1"
echo.
.venv\Scripts\python.exe scripts\verify_local_stack.py
if errorlevel 1 set "FAILED=1"
if "%FAILED%"=="1" (
  echo Stack check failed.
  exit /b 1
)
echo Stack check passed.
endlocal
exit /b 0
