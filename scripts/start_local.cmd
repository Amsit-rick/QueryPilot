@echo off
setlocal
cd /d "%~dp0.."

if not exist ".env" (
  echo Local configuration is missing. Run: scripts\setup_local.cmd
  exit /b 2
)
if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Run: scripts\setup_local.cmd
  exit /b 2
)
if not exist "frontend\node_modules" (
  echo Frontend dependencies are missing. Run: scripts\setup_local.cmd
  exit /b 2
)
.venv\Scripts\python.exe scripts\local_setup.py upgrade-query-credentials
if errorlevel 1 (
  echo Local configuration could not be upgraded for this project version.
  exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running.
  exit /b 2
)

echo [0/5] Checking existing services...

echo [1/5] Starting MySQL...
set "MYSQL_CONTAINER="
for /f "delims=" %%I in ('docker compose -f docker-compose.mysql.yml ps --status running -q mysql 2^>nul') do set "MYSQL_CONTAINER=%%I"
if defined MYSQL_CONTAINER (
  echo MySQL for this Query Pilot checkout is already running; reusing it.
) else (
  docker compose -f docker-compose.mysql.yml up -d
  if errorlevel 1 (
    echo MySQL could not start because port 3307 is already allocated by another service.
    echo Stop that service or change MYSQL_PORT and the Compose port mapping before retrying.
    echo The installer will not reuse an unknown MySQL instance.
    exit /b 1
  )
)

echo [2/5] Waiting for MySQL readiness...
set "MYSQL_READY=0"
for /l %%I in (1,1,30) do (
  docker compose -f docker-compose.mysql.yml exec -T mysql mysqladmin ping -h localhost --silent >nul 2>&1
  if not errorlevel 1 (
    set "MYSQL_READY=1"
    goto mysql_ready
  )
  ping 127.0.0.1 -n 2 >nul
)
:mysql_ready
if "%MYSQL_READY%"=="0" (
  echo MySQL did not become ready within 30 seconds.
  echo Please check: docker compose -f docker-compose.mysql.yml ps
  exit /b 1
)

echo [3/5] Configuring read-only query access and applying database migrations...
docker compose -f docker-compose.mysql.yml exec -T mysql sh /docker-entrypoint-initdb.d/ZZ99-create-readonly-user.sh
if errorlevel 1 (
  echo Failed to configure the dedicated MySQL read-only query account.
  exit /b 1
)
.venv\Scripts\python.exe scripts\mysql_migrate.py
if errorlevel 1 (
  echo Database migrations failed. The API was not started.
  exit /b 1
)

echo [4/5] Running local bundle preflight...
.venv\Scripts\python.exe scripts\verify_local_bundle.py
if errorlevel 1 (
  echo Local preflight failed. Resolve the blocking checks above before starting services.
  exit /b 1
)

rem Local auth still runs the production configuration guard. If an existing
rem .env enables auth but has no metrics token, generate an in-memory token for
rem this launch so the API can start without persisting a secret.
set "LOCAL_AUTH_ENABLED="
set "LOCAL_METRICS_TOKEN="
for /f "tokens=1,* delims==" %%A in ('findstr /b "AUTH_ENABLED=" ".env"') do set "LOCAL_AUTH_ENABLED=%%B"
for /f "tokens=1,* delims==" %%A in ('findstr /b "METRICS_TOKEN=" ".env"') do set "LOCAL_METRICS_TOKEN=%%B"
if /I "%LOCAL_AUTH_ENABLED%"=="true" if "%METRICS_TOKEN%"=="" if "%LOCAL_METRICS_TOKEN%"=="" (
  for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "[Convert]::ToBase64String([byte[]](1..24 | ForEach-Object { Get-Random -Maximum 256 }))"`) do set "METRICS_TOKEN=%%I"
  echo Generated a temporary local metrics token for this launch.
)

echo [5/5] Starting backend on http://127.0.0.1:8000 ...
start "Query Pilot API" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo Starting frontend on http://127.0.0.1:5173 ...
start "Query Pilot Frontend" cmd /k "cd /d "%~dp0..\frontend" && npm.cmd run dev"

echo.
echo Query Pilot is starting.
echo Frontend: http://127.0.0.1:5173
echo Backend:  http://127.0.0.1:8000/docs
echo.
echo Do not run docker-compose.app.yml at the same time.
endlocal
