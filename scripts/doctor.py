import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.startup_checks import validate_production_config
from app.database import database_status

errors = validate_production_config()
report = {"database": database_status(), "production_config_valid": not errors, "errors": errors}
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(1 if errors else 0)
