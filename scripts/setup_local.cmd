@echo off
setlocal
cd /d "%~dp0.."

echo [0/7] Checking required tools...
where py.exe >nul 2>&1 || (echo Python launcher not found. Install Python 3.12. & exit /b 2)
where npm.cmd >nul 2>&1 || (echo npm not found. Install Node.js 22. & exit /b 2)
where docker.exe >nul 2>&1 || (echo Docker not found. Install Docker Desktop. & exit /b 2)
docker info >nul 2>&1 || (echo Docker Desktop is not running. & exit /b 2)

echo [1/7] Preparing Python environment...
if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo [2/7] Installing frontend dependencies...
pushd frontend
call npm.cmd ci
if errorlevel 1 (popd & exit /b 1)
popd

echo [3/7] Creating local configuration...
.venv\Scripts\python.exe scripts\local_setup.py configure
if errorlevel 1 exit /b 1

echo [4/7] Starting MySQL...
docker compose -f docker-compose.mysql.yml up -d
if errorlevel 1 exit /b 1

echo [5/7] Waiting for MySQL...
set "MYSQL_READY=0"
for /l %%I in (1,1,40) do (
  docker compose -f docker-compose.mysql.yml exec -T mysql mysqladmin ping -h localhost --silent >nul 2>&1
  if not errorlevel 1 (
    set "MYSQL_READY=1"
    goto mysql_ready
  )
  ping 127.0.0.1 -n 2 >nul
)
:mysql_ready
if "%MYSQL_READY%"=="0" (echo MySQL did not become ready. & exit /b 1)

echo [6/7] Applying migrations, loading data, configuring read-only access, and creating the local administrator...
.venv\Scripts\python.exe scripts\mysql_migrate.py
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts\seed_demo_data.py --backend mysql --if-empty
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts\load_enterprise_mysql.py --if-empty
if errorlevel 1 exit /b 1
docker compose -f docker-compose.mysql.yml exec -T mysql sh /docker-entrypoint-initdb.d/ZZ99-create-readonly-user.sh
if errorlevel 1 (
  echo Failed to configure the dedicated MySQL read-only query account.
  exit /b 1
)
.venv\Scripts\python.exe scripts\local_setup.py bootstrap-admin
if errorlevel 1 exit /b 1

echo [7/7] Starting Query Pilot...
call scripts\start_local.cmd
endlocal
