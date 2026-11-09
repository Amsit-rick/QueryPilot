"""Create an offline inventory of container image references and digest pinning."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

FORMAT = "query-pilot-container-image-inventory"
VERSION = 1
FROM_PATTERN = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(?P<image>\S+)", re.IGNORECASE)
DIGEST_PATTERN = re.compile(r"@sha256:(?P<digest>[0-9a-fA-F]{64})$")


def _image_entry(*, reference: str, source_file: Path, source_type: str, service: str | None = None) -> dict:
    match = DIGEST_PATTERN.search(reference)
    without_digest = reference.split("@", 1)[0]
    last_segment = without_digest.rsplit("/", 1)[-1]
    tag = last_segment.rsplit(":", 1)[1] if ":" in last_segment else None
    return {
        "source_type": source_type,
        "source_file": source_file.as_posix(),
        "service": service,
        "reference": reference,
        "repository": without_digest.rsplit(":", 1)[0] if tag else without_digest,
        "tag": tag,
        "digest": f"sha256:{match.group('digest').lower()}" if match else None,
        "digest_pinned": match is not None,
    }


def _dockerfile_images(path: Path) -> list[dict]:
    if not path.is_file():
        raise ValueError(f"Dockerfile is missing: {path}")
    images = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = FROM_PATTERN.match(line)
        if not match:
            continue
        reference = match.group("image")
        if reference.startswith("$"):
            raise ValueError("Dockerfile FROM variables cannot be resolved offline")
        images.append(
            _image_entry(
                reference=reference,
                source_file=path,
                source_type="dockerfile_base",
            )
        )
    if not images:
        raise ValueError(f"Dockerfile contains no FROM image: {path}")
    return images


def _compose_images(path: Path) -> list[dict]:
    if not path.is_file():
        raise ValueError(f"Compose file is missing: {path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Compose file is invalid YAML: {path}") from exc
    services = payload.get("services") if isinstance(payload, dict) else None
    if not isinstance(services, dict):
        raise ValueError("Compose file must contain a services object")
    images = []
    for service, config in services.items():
        if not isinstance(config, dict) or not isinstance(config.get("image"), str):
            continue
        reference = config["image"].strip()
        if not reference:
            raise ValueError(f"Compose service {service!r} has an empty image reference")
        images.append(
            _image_entry(
                reference=reference,
                source_file=path,
                source_type="compose_service",
                service=str(service),
            )
        )
    return images


def build_inventory(*, dockerfile: Path, compose: Path) -> dict:
    images = [*_dockerfile_images(dockerfile), *_compose_images(compose)]
    pinned = sum(entry["digest_pinned"] for entry in images)
    return {
        "format": FORMAT,
        "version": VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "image_count": len(images),
        "digest_pinned_count": pinned,
        "unpinned_count": len(images) - pinned,
        "all_digest_pinned": pinned == len(images),
        "scan_status": "not_run",
        "signature_status": "not_verified",
        "approval_status": "not_reviewed",
        "images": images,
        "limitations": [
            "This offline inventory checks reference syntax only and does not pull or inspect images.",
            "Digest pinning does not prove vulnerability scan, signature verification, or release approval.",
        ],
    }


def write_inventory(payload: dict, target: Path, *, protected: set[Path]) -> None:
    if target.resolve() in {path.resolve() for path in protected}:
        raise ValueError("output path must not overwrite an input file")
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
    parser = argparse.ArgumentParser(description="Inventory container image references and digest pinning")
    parser.add_argument("--dockerfile", type=Path, default=Path("Dockerfile"))
    parser.add_argument("--compose", type=Path, default=Path("docker-compose.app.yml"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/image-inventory.json"))
    parser.add_argument("--require-pinned", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = build_inventory(dockerfile=args.dockerfile, compose=args.compose)
        write_inventory(payload, args.output, protected={args.dockerfile, args.compose})
    except (OSError, ValueError) as exc:
        print(f"Image inventory failed: {exc}")
        return 2
    print(
        f"Image inventory written: {args.output} "
        f"({payload['digest_pinned_count']}/{payload['image_count']} digest pinned)"
    )
    if args.require_pinned and not payload["all_digest_pinned"]:
        print("Image inventory contains unpinned references; pin them before production release.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
