import sqlite3
from pathlib import Path
from .backup import backup_database
def restore_database(backup_path, target_path):
    source=Path(backup_path); target=Path(target_path)
    if not source.exists(): raise FileNotFoundError(str(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists(): backup_database(str(target), str(target.parent / "backups"))
    with sqlite3.connect(source) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise ValueError("备份完整性校验失败")
    target.write_bytes(source.read_bytes()); return str(target)
