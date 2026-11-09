@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0.."

if not exist ".env" (
  echo Local configuration is missing. Run: scripts\setup_local.cmd
  exit /b 1
)

set "QUERY_PILOT_MCP_TOKEN="
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
  if /I "%%A"=="API_KEYS" set "QUERY_PILOT_MCP_TOKEN=%%B"
)

if not defined QUERY_PILOT_MCP_TOKEN (
  echo API_KEYS is missing or empty in .env.
  exit /b 1
)

for /f "tokens=1 delims=," %%K in ("%QUERY_PILOT_MCP_TOKEN%") do set "QUERY_PILOT_MCP_TOKEN=%%K"

if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Run: scripts\setup_local.cmd
  exit /b 1
)

".venv\Scripts\python.exe" scripts\run_mcp_server.py --transport stdio
