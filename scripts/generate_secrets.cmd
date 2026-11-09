@echo off
setlocal
python -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(48)); print('METRICS_TOKEN=' + secrets.token_urlsafe(32)); print('MYSQL_PASSWORD=' + secrets.token_urlsafe(32)); print('MYSQL_ROOT_PASSWORD=' + secrets.token_urlsafe(32)); print('MYSQL_QUERY_PASSWORD=' + secrets.token_urlsafe(32))"
echo.
echo Copy these values into your secret manager or local .env. Do not commit them.
