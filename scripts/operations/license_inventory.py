"""Create an offline license inventory from a CycloneDX SBOM."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "query-pilot-license-inventory"
VERSION = 1


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _license_names(component: dict) -> list[str]:
    values = []
    for item in component.get("licenses", []):
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("expression"), str) and item["expression"].strip():
            values.append(item["expression"].strip())
        license_data = item.get("license")
        if isinstance(license_data, dict):
            value = license_data.get("id") or license_data.get("name")
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
    return sorted(set(values))


def build_inventory(sbom_path: Path) -> dict:
    if not sbom_path.is_file() or sbom_path.stat().st_size == 0:
        raise ValueError(f"SBOM file is missing or empty: {sbom_path}")
    try:
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("SBOM is not valid JSON") from exc
    if not isinstance(sbom, dict) or sbom.get("bomFormat") != "CycloneDX":
        raise ValueError("SBOM must be a CycloneDX document")
    components = sbom.get("components")
    if not isinstance(components, list):
        raise ValueError("SBOM components must be a list")
    if not components:
        raise ValueError("SBOM must contain at least one component")
    license_counts: dict[str, int] = {}
    unknown = []
    component_inventory = []
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            raise ValueError(f"SBOM component at index {index} must be an object")
        licenses = _license_names(component)
        purl = component.get("purl") or component.get("bom-ref")
        entry = {
            "name": component.get("name"),
            "version": component.get("version"),
            "purl": purl,
            "licenses": licenses,
        }
        component_inventory.append(entry)
        if not licenses:
            unknown.append(purl or component.get("name") or "unknown-component")
        for license_name in licenses:
            license_counts[license_name] = license_counts.get(license_name, 0) + 1
    return {
        "format": FORMAT,
        "version": VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sbom": sbom_path.name,
        "source_sbom_sha256": _sha256_file(sbom_path),
        "component_count": len(component_inventory),
        "declared_license_count": len(component_inventory) - len(unknown),
        "unknown_license_count": len(unknown),
        "inventory_complete": not unknown,
        "approval_status": "not_reviewed",
        "manual_review_required": True,
        "license_counts": dict(sorted(license_counts.items())),
        "unknown_components": sorted(str(item) for item in unknown),
        "components": component_inventory,
        "limitations": [
            "License names come from package metadata and may be incomplete or non-SPDX.",
            "This inventory is not legal approval and does not apply an allow/deny policy.",
        ],
    }


def write_inventory(payload: dict, target: Path, sbom_path: Path) -> None:
    if target.resolve() == sbom_path.resolve():
        raise ValueError("output path must not overwrite the SBOM")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a license inventory from a CycloneDX SBOM")
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/license-inventory.json"))
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = build_inventory(args.sbom)
        write_inventory(payload, args.output, args.sbom)
    except (OSError, ValueError) as exc:
        print(f"License inventory failed: {exc}")
        return 2
    print(
        f"License inventory written: {args.output} "
        f"({payload['component_count']} components, {payload['unknown_license_count']} unknown)"
    )
    if args.require_complete and not payload["inventory_complete"]:
        print("License inventory is incomplete; manual metadata review is required.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
