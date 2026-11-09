import json

from scripts.generate_sbom import build_sbom, npm_components


def test_npm_components_are_read_from_lockfile(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "packages": {
                    "": {"dependencies": {"vue": "3.5.42"}},
                    "node_modules/vue": {
                        "version": "3.5.42",
                        "integrity": "sha512-example",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    components = npm_components(lock)

    assert components[0]["name"] == "vue"
    assert components[0]["purl"] == "pkg:npm/vue@3.5.42"
    assert any(item["name"] == "query-pilot:npm-integrity" for item in components[0]["properties"])


def test_npm_components_normalize_scoped_and_nested_package_paths(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/@playwright/test": {"version": "1.62.1"},
                    "node_modules/playwright/node_modules/fsevents": {"version": "2.3.2"},
                }
            }
        ),
        encoding="utf-8",
    )

    components = npm_components(lock)

    assert [item["name"] for item in components] == ["@playwright/test", "fsevents"]
    assert components[0]["purl"] == "pkg:npm/%40playwright/test@1.62.1"


def test_sbom_has_cyclonedx_metadata_and_deduplicates_components(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package-lock.json").write_text('{"packages":{}}', encoding="utf-8")
    component = {
        "type": "library",
        "name": "fastapi",
        "version": "1.0.0",
        "purl": "pkg:pypi/fastapi@1.0.0",
        "bom-ref": "pkg:pypi/fastapi@1.0.0",
    }

    payload = build_sbom(root=tmp_path, python=[component, component])

    assert payload["bomFormat"] == "CycloneDX"
    assert payload["specVersion"] == "1.5"
    assert payload["metadata"]["component"]["name"] == "query-pilot"
    assert payload["components"] == [component]
