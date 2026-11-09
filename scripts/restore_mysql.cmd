@echo off
setlocal
if "%MYSQL_CONTAINER%"=="" set MYSQL_CONTAINER=data_query_agent-mysql-1
if "%MYSQL_ROOT_PASSWORD%"=="" (
  echo MYSQL_ROOT_PASSWORD must be set by the calling environment.
  exit /b 2
)
set "MYSQL_PWD=%MYSQL_ROOT_PASSWORD%"
if "%~1"=="" (echo Usage: restore_mysql.cmd backup.sql & exit /b 2)
if not exist "%~1" (echo Backup file not found: %~1 & exit /b 2)
echo WARNING: this will overwrite data in query_agent.
choice /M "Continue"
if errorlevel 2 (
  set "MYSQL_PWD="
  exit /b 1
)
docker exec -i -e MYSQL_PWD "%MYSQL_CONTAINER%" mysql -u root query_agent < "%~1"
if errorlevel 1 (
  set "MYSQL_PWD="
  echo Restore failed
  exit /b 1
)
set "MYSQL_PWD="
echo Restore completed.
