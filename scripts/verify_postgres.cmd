@echo off
setlocal
set POSTGRES_DSN=postgresql://query_readonly:query_readonly_dev@127.0.0.1:5433/query_agent
set DATABASE_BACKEND=postgres
.venv\Scripts\python.exe -c "from app.postgres_db import test_postgres,postgres_schema; status=test_postgres(); schema=postgres_schema(); print(status); print(schema); assert status['ok']; assert {'sales','customers'} <= set(schema), 'required business tables are missing'"
