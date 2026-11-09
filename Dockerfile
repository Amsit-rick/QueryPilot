FROM python:3.12-slim
WORKDIR /app
ENV PYTHONPATH=/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data ./data
COPY scripts ./scripts
COPY migrations ./migrations
RUN groupadd --system querypilot \
    && useradd --system --gid querypilot --home-dir /app --no-create-home querypilot \
    && mkdir -p /app/data/exports \
    && chown -R querypilot:querypilot /app
USER querypilot
EXPOSE 8000
CMD ["sh", "-c", "python scripts/mysql_migrate.py && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
