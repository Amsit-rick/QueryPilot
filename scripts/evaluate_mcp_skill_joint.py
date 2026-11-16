from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.mcp_skill_joint_evaluation import evaluate_mcp_skill_joint_cases, load_mcp_skill_joint_cases
from app.services.skill_service import SkillService


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Query Pilot Skill/MCP contract cases")
    parser.add_argument("--dataset", default=str(ROOT / "data" / "mcp_skill_joint_eval.json"))
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    version, cases = load_mcp_skill_joint_cases(args.dataset)
    report = evaluate_mcp_skill_joint_cases(
        cases=cases,
        skill_service=SkillService(),
        dataset_version=version,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if report["summary"]["passed"] == report["summary"]["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
