"""Compare a candidate Skill catalog with the built-in routing baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.skill_evaluation import builtin_catalog, compare_skill_catalogs, overlay_candidate_catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, help="JSON file containing a Skill definition or list of definitions")
    parser.add_argument("--cases", default=str(ROOT / "data" / "skill_routing_eval.json"))
    parser.add_argument("--output", help="Optional path for the JSON report")
    args = parser.parse_args()
    candidate_payload = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    if isinstance(candidate_payload, dict):
        candidate_payload = candidate_payload.get("skills", [candidate_payload])
    if not isinstance(candidate_payload, list):
        parser.error("candidate JSON must contain a Skill object or skills array")
    cases_payload = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    report = compare_skill_catalogs(
        baseline=builtin_catalog(),
        candidate=overlay_candidate_catalog(tuple_from_builtin(), candidate_payload),
        cases=cases_payload.get("cases", []),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


def tuple_from_builtin():
    from app.services.skill_service import BUILTIN_SKILLS

    return BUILTIN_SKILLS


if __name__ == "__main__":
    raise SystemExit(main())
