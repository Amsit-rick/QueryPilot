"""Generate an offline CycloneDX SBOM from installed Python and npm lock data."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _clean_license(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized or normalized.upper() in {"UNKNOWN", "NONE", "N/A"}:
        return None
    return normalized[:200]


def _license_entries(values: list[str]) -> list[dict]:
    return [{"license": {"name": value}} for value in sorted(set(values))]


def _python_license_names(distribution) -> list[str]:
    metadata = distribution.metadata
    expression = _clean_license(metadata.get("License-Expression"))
    if expression:
        return [expression]
    values = []
    for classifier in metadata.get_all("Classifier", []):
        if classifier.startswith("License ::"):
            cleaned = _clean_license(classifier.split("::")[-1])
            if cleaned:
                values.append(cleaned)
    if values:
        return sorted(set(values))
    legacy = _clean_license(metadata.get("License"))
    if legacy:
        values.append(legacy)
    return sorted(set(values))


def python_components() -> list[dict]:
    components = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        version = distribution.version
        if not name or not version:
            continue
        normalized = name.lower().replace("_", "-")
        component = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{quote(normalized)}@{quote(version)}",
            "bom-ref": f"pkg:pypi/{quote(normalized)}@{quote(version)}",
            "properties": [{"name": "query-pilot:ecosystem", "value": "python"}],
        }
        licenses = _python_license_names(distribution)
        if licenses:
            component["licenses"] = _license_entries(licenses)
        components.append(component)
    return components


def npm_components(lock_path: Path) -> list[dict]:
    if not lock_path.exists():
        return []
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    components = []
    for path, package in payload.get("packages", {}).items():
        if not path.startswith("node_modules/") or not isinstance(package, dict):
            continue
        name = path.rsplit("node_modules/", 1)[-1]
        version = package.get("version")
        if not version:
            continue
        if name.startswith("@") and "/" in name:
            namespace, package_name = name.split("/", 1)
            encoded_name = f"{quote(namespace, safe='')}/{quote(package_name, safe='')}"
        else:
            encoded_name = quote(name, safe="")
        purl = f"pkg:npm/{encoded_name}@{quote(str(version))}"
        component = {
            "type": "library",
            "name": name,
            "version": str(version),
            "purl": purl,
            "bom-ref": purl,
            "properties": [{"name": "query-pilot:ecosystem", "value": "npm"}],
        }
        if package.get("integrity"):
            component["properties"].append(
                {"name": "query-pilot:npm-integrity", "value": str(package["integrity"])}
            )
        raw_license = package.get("license")
        license_values = raw_license if isinstance(raw_license, list) else [raw_license]
        licenses = [value for value in (_clean_license(item) for item in license_values) if value]
        if licenses:
            component["licenses"] = _license_entries(licenses)
        components.append(component)
    return components


def build_sbom(*, root: Path = PROJECT_ROOT, python: list[dict] | None = None) -> dict:
    components = (python if python is not None else python_components()) + npm_components(
        root / "frontend" / "package-lock.json"
    )
    unique = {component["bom-ref"]: component for component in components}
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "query-pilot",
                "version": "0.1.0",
                "bom-ref": "pkg:generic/query-pilot@0.1.0",
            },
            "properties": [
                {
                    "name": "query-pilot:scope",
                    "value": "installed Python environment and frontend package-lock.json",
                }
            ],
        },
        "components": sorted(unique.values(), key=lambda item: item["bom-ref"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Query Pilot CycloneDX SBOM")
    parser.add_argument("--output", default="artifacts/sbom.cdx.json")
    args = parser.parse_args(argv)
    output = PROJECT_ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_sbom(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"SBOM written: {output.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
