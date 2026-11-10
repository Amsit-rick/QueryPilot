@echo off
setlocal
cd /d "%~dp0.."

if not exist ".env" (
  echo Local configuration is missing. Run: scripts\setup_local.cmd
  exit /b 2
)
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running.
  exit /b 2
)

echo Starting MySQL 8...
set "MYSQL_CONTAINER="
for /f "delims=" %%I in ('docker compose -f docker-compose.mysql.yml ps --status running -q mysql 2^>nul') do set "MYSQL_CONTAINER=%%I"
if defined MYSQL_CONTAINER (
  echo MySQL for this Query Pilot checkout is already running; reusing it.
) else (
  docker compose -f docker-compose.mysql.yml up -d
  if errorlevel 1 exit /b 1
)
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
  exit /b 1
)

set "POSTGRES_READONLY_DSN="
set "SQLSERVER_HOST="
set "SQLSERVER_ADMIN_PASSWORD="
set "CLICKHOUSE_HOST="
for /f "tokens=1,* delims==" %%A in ('findstr /b "POSTGRES_READONLY_DSN=" ".env"') do set "POSTGRES_READONLY_DSN=%%B"
for /f "tokens=1,* delims==" %%A in ('findstr /b "SQLSERVER_HOST=" ".env"') do set "SQLSERVER_HOST=%%B"
for /f "tokens=1,* delims==" %%A in ('findstr /b "SQLSERVER_ADMIN_PASSWORD=" ".env"') do set "SQLSERVER_ADMIN_PASSWORD=%%B"
for /f "tokens=1,* delims==" %%A in ('findstr /b "CLICKHOUSE_HOST=" ".env"') do set "CLICKHOUSE_HOST=%%B"

if not "%POSTGRES_READONLY_DSN%"=="" (
  echo Starting PostgreSQL 16...
  docker compose -p query-pilot-postgres -f docker-compose.postgres.yml up -d
  if errorlevel 1 exit /b 1
  .venv\Scripts\python.exe scripts\seed_multimonth_demo_data.py --postgres-container query-pilot-postgres-postgres-1 --wait-seconds 30
  if errorlevel 1 (
    echo PostgreSQL did not become ready or demo data seeding failed.
    exit /b 1
  )
)

if not "%SQLSERVER_HOST%"=="" (
  echo Starting SQL Server 2022...
  if "%SQLSERVER_ADMIN_PASSWORD%"=="" (
    echo SQLSERVER_ADMIN_PASSWORD is required to create the SQL Server container.
    exit /b 1
  )
  set "MSSQL_SA_PASSWORD=%SQLSERVER_ADMIN_PASSWORD%"
  docker compose -p query-pilot-sqlserver -f docker-compose.sqlserver.yml up -d
  if errorlevel 1 exit /b 1
  set "SQLSERVER_READY=0"
  for /l %%I in (1,1,40) do (
    for /f "delims=" %%H in ('docker inspect query-pilot-sqlserver-sqlserver-1 --format "{{.State.Health.Status}}" 2^>nul') do (
      if /I "%%H"=="healthy" (
        set "SQLSERVER_READY=1"
        goto sqlserver_ready
      )
    )
    ping 127.0.0.1 -n 3 >nul
  )
  :sqlserver_ready
  if "%SQLSERVER_READY%"=="0" (
    echo SQL Server did not become healthy within 80 seconds.
    exit /b 1
  )
  .venv\Scripts\python.exe scripts\provision_sqlserver.py
  if errorlevel 1 exit /b 1
)

if not "%CLICKHOUSE_HOST%"=="" (
  echo Starting ClickHouse 24.8...
  docker inspect qp-clickhouse >nul 2>&1
  if errorlevel 1 (
    echo ClickHouse container qp-clickhouse is missing. Provision it before enabling START_ALL_DATASOURCES.
    exit /b 1
  )
  docker start qp-clickhouse >nul
  if errorlevel 1 exit /b 1
  set "CLICKHOUSE_READY=0"
  for /l %%I in (1,1,30) do (
    docker exec qp-clickhouse clickhouse-client --query "SELECT 1" >nul 2>&1
    if not errorlevel 1 (
      set "CLICKHOUSE_READY=1"
      goto clickhouse_ready
    )
    ping 127.0.0.1 -n 2 >nul
  )
  :clickhouse_ready
  if "%CLICKHOUSE_READY%"=="0" (
    echo ClickHouse did not become ready within 30 seconds.
    exit /b 1
  )
  .venv\Scripts\python.exe scripts\provision_clickhouse.py --container qp-clickhouse
  if errorlevel 1 exit /b 1
)

echo Verifying every configured datasource with a real read-only query...
.venv\Scripts\python.exe scripts\verify_configured_datasources.py --wait-seconds 120
if errorlevel 1 (
  echo One or more configured datasources are not ready.
  exit /b 1
)

echo All configured datasources are ready.
endlocal
