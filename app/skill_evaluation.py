"""Offline comparison of deterministic Skill routing releases."""

from __future__ import annotations

from typing import Any

from app.domain.skill import SkillDefinition
from app.repositories.memory_skill_repository import InMemorySkillRepository
from app.services.skill_service import BUILTIN_SKILLS, SkillService


def _service(definitions: list[dict[str, Any]]) -> SkillService:
    skills = tuple(SkillDefinition.model_validate(item) for item in definitions)
    return SkillService(skills=skills, repository=InMemorySkillRepository())


def compare_skill_catalogs(
    *,
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare routing behavior; this is not a SQL or business-accuracy score."""
    case_ids = [str(case.get("id", "")).strip() for case in cases]
    if any(not value for value in case_ids) or len(set(case_ids)) != len(case_ids):
        raise ValueError("evaluation case IDs must be non-empty and unique")
    if not cases:
        raise ValueError("at least one evaluation case is required")

    before = _service(baseline)
    after = _service(candidate)
    results = []
    baseline_matches = 0
    candidate_matches = 0
    route_changes = 0
    version_changes = 0
    for case in cases:
        question = str(case.get("question", "")).strip()
        if not question or len(question) > 2000:
            raise ValueError(f"invalid question in evaluation case {case['id']}")
        expected = case.get("expected_skill_id")
        old = before.route(question)
        new = after.route(question)
        old_skill = old.get("selected_skill")
        new_skill = new.get("selected_skill")
        old_id = old_skill["id"] if old_skill else None
        new_id = new_skill["id"] if new_skill else None
        baseline_matches += old_id == expected
        candidate_matches += new_id == expected
        route_changed = old_id != new_id or old["evidence"]["matched_terms"] != new["evidence"]["matched_terms"]
        version_changed = (old_skill or {}).get("version") != (new_skill or {}).get("version")
        route_changes += route_changed
        version_changes += version_changed
        results.append({
            "id": case["id"],
            "expected_skill_id": expected,
            "baseline": {
                "skill_id": old_id,
                "version": old_skill.get("version") if old_skill else None,
                "matched_terms": old["evidence"]["matched_terms"],
            },
            "candidate": {
                "skill_id": new_id,
                "version": new_skill.get("version") if new_skill else None,
                "matched_terms": new["evidence"]["matched_terms"],
            },
            "route_changed": route_changed,
            "version_changed": version_changed,
        })

    count = len(cases)
    return {
        "evaluation_type": "skill_routing_only",
        "summary": {
            "cases": count,
            "route_changes": route_changes,
            "version_changes": version_changes,
            "baseline_matches": baseline_matches,
            "candidate_matches": candidate_matches,
            "baseline_match_rate": baseline_matches / count,
            "candidate_match_rate": candidate_matches / count,
        },
        "cases": results,
    }


def overlay_candidate_catalog(
    baseline: tuple[SkillDefinition, ...], candidate: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replace only explicitly supplied IDs, retaining the rest of the baseline."""
    current = {item.id: item.model_dump(mode="json") for item in baseline}
    for raw in candidate:
        definition = SkillDefinition.model_validate(raw)
        current[definition.id] = definition.model_dump(mode="json")
    return [current[key] for key in sorted(current)]


def builtin_catalog() -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in BUILTIN_SKILLS]


__all__ = ["builtin_catalog", "compare_skill_catalogs", "overlay_candidate_catalog"]
