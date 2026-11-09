@echo off
setlocal

if "%~1"=="" (
    echo Usage: verify_backup_mysql.cmd backup.sql
    exit /b 2
)

set "FILE=%~1"
if not exist "%FILE%" (
    echo Backup file not found: %FILE%
    exit /b 2
)

for %%A in ("%FILE%") do set "SIZE=%%~zA"
if "%SIZE%"=="" set "SIZE=0"
if %SIZE% LEQ 0 (
    echo Backup file is empty: %FILE%
    exit /b 1
)

findstr /I /C:"-- MySQL dump" "%FILE%" >nul
if errorlevel 1 (
    echo Backup header not found: %FILE%
    exit /b 1
)

findstr /I /C:"CREATE TABLE" "%FILE%" >nul
if errorlevel 1 (
    echo No CREATE TABLE statement found: %FILE%
    exit /b 1
)

findstr /I /C:"schema_migrations" "%FILE%" >nul
if errorlevel 1 (
    echo schema_migrations table not found: %FILE%
    exit /b 1
)

echo Backup verification passed: %FILE%
exit /b 0
