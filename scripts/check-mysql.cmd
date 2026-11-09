@echo off
cd /d %~dp0..
docker compose -f docker-compose.mysql.yml up -d
if errorlevel 1 exit /b 1
echo MySQL container started on 127.0.0.1:3307
