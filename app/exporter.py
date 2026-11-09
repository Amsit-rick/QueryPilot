import csv
import uuid
from pathlib import Path
from .db import execute_readonly


def export_csv(db_path: str, sql: str, params: dict, export_dir: str, max_rows: int, timeout: float) -> tuple[str, int]:
    columns, rows, _ = execute_readonly(db_path, sql, max_rows, params, timeout)
    directory = Path(export_dir); directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"query-{uuid.uuid4()}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle); writer.writerow(columns); writer.writerows(rows)
    return str(path), len(rows)


def write_csv(columns: list[str], rows: list[list], export_dir: str) -> tuple[str, int]:
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"query-{uuid.uuid4()}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)
    return str(path), len(rows)
