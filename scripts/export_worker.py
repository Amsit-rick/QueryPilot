"""Redis export worker for EXPORT_EXECUTION_MODE=redis."""
import logging
import os
from app.config import settings
from app.datasources import get_datasource, execute_datasource
from app.export_queue import dequeue_export, enqueue_dead_letter
from app.export_store import get_job_payload, update_job, recover_stale_jobs
from app.exporter import write_csv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def process(job_id: str) -> None:
    job = get_job_payload(job_id)
    if not job or job["status"] != "queued":
        return
    update_job(job_id, status="running")
    last_error = None
    for attempt in range(settings.export_max_retries + 1):
        try:
            datasource = get_datasource(job["datasource_id"])
            if not datasource:
                raise RuntimeError("数据源不存在")
            columns, rows, _ = execute_datasource(datasource, job["sql"], job["params"], settings.max_rows, 0)
            path, count = write_csv(columns, rows, settings.export_dir)
            update_job(job_id, status="completed", file_path=path, row_count=count)
            return
        except Exception as exc:
            last_error = exc
            if attempt < settings.export_max_retries:
                logging.warning("export job %s attempt %s failed; retrying", job_id, attempt + 1)
    logging.exception("export job %s failed after retries", job_id)
    update_job(job_id, status="failed", error_message=str(last_error))
    try:
        enqueue_dead_letter(settings.redis_url, job_id, str(last_error))
    except Exception:
        logging.exception("failed to write dead-letter record for %s", job_id)

def main() -> None:
    if settings.export_execution_mode != "redis":
        raise SystemExit("EXPORT_EXECUTION_MODE 必须设置为 redis")
    try:
        recovered = recover_stale_jobs()
        logging.info("export worker started; recovered %s stale jobs", recovered)
    except Exception:
        logging.exception("stale job recovery failed")
    while True:
        job_id = dequeue_export(settings.redis_url, timeout=5)
        if job_id:
            process(job_id)

if __name__ == "__main__":
    main()
