import json

from scripts.operations.image_inventory import build_inventory, main


def _inputs(tmp_path, *, base="python:3.12-slim"):
    dockerfile = tmp_path / "Dockerfile"
    compose = tmp_path / "compose.yml"
    dockerfile.write_text(f"FROM {base}\n", encoding="utf-8")
    compose.write_text(
        """services:
  mysql:
    image: mysql:8.4@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  redis:
    image: redis:7-alpine@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
  api:
    build: .
""",
        encoding="utf-8",
    )
    return dockerfile, compose


def test_image_inventory_reports_pinned_and_unpinned_references(tmp_path):
    dockerfile, compose = _inputs(tmp_path)

    inventory = build_inventory(dockerfile=dockerfile, compose=compose)

    assert inventory["image_count"] == 3
    assert inventory["digest_pinned_count"] == 2
    assert inventory["unpinned_count"] == 1
    assert inventory["all_digest_pinned"] is False
    assert inventory["scan_status"] == "not_run"
    assert inventory["signature_status"] == "not_verified"
    assert inventory["approval_status"] == "not_reviewed"
    assert {item["service"] for item in inventory["images"]} == {None, "mysql", "redis"}


def test_image_inventory_strict_mode_writes_report_then_fails(tmp_path):
    dockerfile, compose = _inputs(tmp_path)
    output = tmp_path / "inventory.json"

    code = main(
        [
            "--dockerfile",
            str(dockerfile),
            "--compose",
            str(compose),
            "--output",
            str(output),
            "--require-pinned",
        ]
    )

    assert code == 1
    assert json.loads(output.read_text(encoding="utf-8"))["unpinned_count"] == 1


def test_image_inventory_strict_mode_passes_when_all_images_are_pinned(tmp_path):
    digest = "c" * 64
    dockerfile, compose = _inputs(tmp_path, base=f"python:3.12-slim@sha256:{digest}")

    assert main(
        [
            "--dockerfile",
            str(dockerfile),
            "--compose",
            str(compose),
            "--output",
            str(tmp_path / "inventory.json"),
            "--require-pinned",
        ]
    ) == 0


def test_image_inventory_refuses_to_overwrite_an_input(tmp_path):
    dockerfile, compose = _inputs(tmp_path)
    original = dockerfile.read_text(encoding="utf-8")

    assert main(
        [
            "--dockerfile",
            str(dockerfile),
            "--compose",
            str(compose),
            "--output",
            str(dockerfile),
        ]
    ) == 2
    assert dockerfile.read_text(encoding="utf-8") == original
