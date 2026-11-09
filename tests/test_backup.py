from app.db import init_db
from app.backup import backup_database


def test_database_backup_is_created(tmp_path):
    source = tmp_path / "demo.db"; init_db(str(source))
    target = backup_database(str(source), str(tmp_path / "backups"))
    assert target.endswith(".db")
