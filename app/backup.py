import shutil
from datetime import datetime, timezone
from pathlib import Path


def backup_database(database_path: str, backup_dir: str = "./data/backups") -> str:
    source = Path(database_path)
    if not source.exists():
        raise FileNotFoundError(str(source))
    target_dir = Path(backup_dir); target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source.stem}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{source.suffix}"
    shutil.copy2(source, target)
    return str(target)
