import json

from scripts.generate_sbom import _python_license_names, build_sbom, npm_components
from scripts.operations.license_inventory import build_inventory, main


class _Metadata(dict):
    def get_all(self, key, default=None):
        return self.get(key, default or [])


class _Distribution:
    metadata = _Metadata(
        {
            "License": "UNKNOWN",
            "License-Expression": "Apache-2.0",
            "Classifier": ["License :: OSI Approved :: Apache Software License"],
        }
    )


def test_python_license_names_prefer_declared_metadata_and_classifiers():
    assert _python_license_names(_Distribution()) == ["Apache-2.0"]


def test_npm_components_include_lockfile_license(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/example": {
                        "version": "1.2.3",
                        "license": "MIT",
                        "integrity": "sha512-example",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    components = npm_components(lock)

    assert components[0]["licenses"] == [{"license": {"name": "MIT"}}]


def test_license_inventory_reports_unknown_without_claiming_approval(tmp_path):
    sbom_path = tmp_path / "sbom.json"
    sbom = build_sbom(
        root=tmp_path,
        python=[
            {
                "type": "library",
                "name": "known",
                "version": "1",
                "bom-ref": "known@1",
                "purl": "pkg:pypi/known@1",
                "licenses": [{"license": {"name": "MIT"}}],
            },
            {
                "type": "library",
                "name": "unknown",
                "version": "1",
                "bom-ref": "unknown@1",
                "purl": "pkg:pypi/unknown@1",
            },
        ],
    )
    sbom_path.write_text(json.dumps(sbom), encoding="utf-8")

    inventory = build_inventory(sbom_path)

    assert inventory["component_count"] == 2
    assert inventory["unknown_license_count"] == 1
    assert inventory["inventory_complete"] is False
    assert inventory["approval_status"] == "not_reviewed"
    assert inventory["manual_review_required"] is True


def test_license_inventory_strict_mode_returns_nonzero_for_unknown(tmp_path):
    sbom = tmp_path / "sbom.json"
    output = tmp_path / "licenses.json"
    sbom.write_text(
        json.dumps({"bomFormat": "CycloneDX", "components": [{"name": "unknown"}]}),
        encoding="utf-8",
    )

    code = main(["--sbom", str(sbom), "--output", str(output), "--require-complete"])

    assert code == 1
    assert json.loads(output.read_text(encoding="utf-8"))["approval_status"] == "not_reviewed"


def test_license_inventory_refuses_to_overwrite_sbom(tmp_path):
    sbom = tmp_path / "sbom.json"
    original = json.dumps(
        {
            "bomFormat": "CycloneDX",
            "components": [
                {"name": "known", "licenses": [{"license": {"name": "MIT"}}]}
            ],
        }
    )
    sbom.write_text(original, encoding="utf-8")

    assert main(["--sbom", str(sbom), "--output", str(sbom)]) == 2
    assert sbom.read_text(encoding="utf-8") == original


def test_license_inventory_rejects_empty_or_malformed_component_lists(tmp_path):
    empty = tmp_path / "empty.json"
    malformed = tmp_path / "malformed.json"
    empty.write_text(json.dumps({"bomFormat": "CycloneDX", "components": []}), encoding="utf-8")
    malformed.write_text(
        json.dumps({"bomFormat": "CycloneDX", "components": ["not-an-object"]}),
        encoding="utf-8",
    )

    assert main(["--sbom", str(empty), "--output", str(tmp_path / "empty-out.json")]) == 2
    assert main(["--sbom", str(malformed), "--output", str(tmp_path / "bad-out.json")]) == 2
