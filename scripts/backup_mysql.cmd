@echo off
setlocal
if "%MYSQL_CONTAINER%"=="" set MYSQL_CONTAINER=data_query_agent-mysql-1
if "%MYSQL_ROOT_PASSWORD%"=="" (
  echo MYSQL_ROOT_PASSWORD must be set by the calling environment.
  exit /b 2
)
set "MYSQL_PWD=%MYSQL_ROOT_PASSWORD%"
set OUT_DIR=%~1
if "%OUT_DIR%"=="" set OUT_DIR=backups
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')"`) do set "STAMP=%%I"
if "%STAMP%"=="" (echo Could not create UTC backup timestamp & exit /b 1)
set "FILE=%OUT_DIR%\query_agent_%STAMP%.sql"
docker exec -e MYSQL_PWD "%MYSQL_CONTAINER%" mysqldump -u root --single-transaction --routines --triggers query_agent > "%FILE%"
if errorlevel 1 (
  del /q "%FILE%" >nul 2>&1
  echo Backup failed
  exit /b 1
)
set "MYSQL_PWD="
echo Backup written to %FILE%
