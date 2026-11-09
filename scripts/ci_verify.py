"""Local and CI parity gate for Query Pilot.

Runs the same checks a merge must pass, so a developer can reproduce CI
failures locally instead of discovering them after pushing.

Standard library only: this script must run before project dependencies are
installed, because one of its jobs is verifying those dependencies are safe.

Usage:
    python scripts/ci_verify.py
    python scripts/ci_verify.py --skip-frontend --report artifacts/ci-report.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if sys.platform == "win32":
    # Windows CMD may use a GBK console; gate output contains Unicode status
    # markers and Chinese diagnostics, so prefer UTF-8 when available.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

MIGRATION_PATTERN = re.compile(r"^V(\d{3})__[A-Za-z0-9_]+\.sql$")
# Statements that must never appear in an auto-applied migration.
FORBIDDEN_MIGRATION_STATEMENTS = (
    "DROP DATABASE",
    "DROP SCHEMA",
    "TRUNCATE",
    "GRANT ALL",
)

SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*[\"'][^\"'\s]{16,}[\"']"),
    re.compile(r"(?i)\bpassword\s*[:=]\s*[\"'](?!\{)(?!\s*\$)[^\"'\s]{12,}[\"']"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

# Matches that look like secrets but are documentation or fixtures.
SECRET_ALLOWLIST = (
    "example",
    "placeholder",
    "dummy",
    "changeme",
    "your-",
    "xxx",
    "****",
    "<",
    "{",
)

SCAN_SUFFIXES = {".py", ".js", ".vue", ".json", ".yml", ".yaml", ".conf", ".sql", ".cmd", ".sh"}
SCAN_SKIP_DIRS = {
    ".venv",
    "node_modules",
    ".git",
    "__pycache__",
    "dist",
    "build",
    ".pytest_cache",
    "artifacts",
}


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str = ""
    warnings: list[str] = field(default_factory=list)
    executed: bool = True
    blocking: bool = True

    @property
    def status(self) -> str:
        return "PASS" if self.passed else "FAIL"


def _pytest_summary(output: str) -> str:
    """Pick the pytest result line, ignoring unrelated trailing noise.

    Some sandboxes append a traceback while pytest cleans temp directories.
    Taking the last line blindly would report that noise as the test result.
    """
    for line in reversed(output.splitlines()):
        if re.search(r"\d+\s+(passed|failed|error)", line):
            return line.strip()
    lines = output.splitlines()
    return lines[-1].strip() if lines else "无输出"


def _run(
    command: list[str], cwd: Path | None = None, timeout: int | None = None
) -> tuple[int, str]:
    # Windows commonly exposes npm/npx as .cmd shims rather than executable
    # binaries. Resolve the shim explicitly so the local gate matches CI.
    if sys.platform == "win32" and command:
        if command[0] == "npm":
            command = ["npm.cmd", *command[1:]]
        elif command[0] == "npx":
            command = ["npx.cmd", *command[1:]]
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd or PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, f"命令不存在: {command[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"命令执行超时（{timeout} 秒）: {command[0]}"
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, output.strip()


def _looks_like_code_identifier(snippet: str) -> bool:
    """Distinguish a handler name from a real credential.

    A framework attribute like ``password="changePassword"`` is a function
    reference, not a secret. Real credentials contain digits or symbols.
    """
    match = re.search(r"[\"']([^\"']+)[\"']", snippet)
    if not match:
        return False
    value = match.group(1)
    if any(character.isdigit() for character in value):
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,40}", value))


def check_migrations() -> GateResult:
    """Migrations must be uniquely numbered per dialect and never destructive.

    Version numbers are scoped to their dialect directory: V001 under ``mysql``
    and V001 under ``oracle`` are independent histories. Files under a ``dba``
    directory are applied by a DBA by hand and are exempt from naming rules.
    """
    root = PROJECT_ROOT / "migrations"
    if not root.exists():
        return GateResult("迁移静态检查", True, "未找到 migrations 目录，跳过", executed=False)

    seen: dict[tuple[str, str], list[Path]] = {}
    warnings: list[str] = []
    failures: list[str] = []
    scanned = 0

    for path in sorted(root.rglob("*.sql")):
        scanned += 1
        relative = path.relative_to(PROJECT_ROOT)
        parts = {part.lower() for part in path.parts}

        text = path.read_text(encoding="utf-8", errors="replace").upper()
        for statement in FORBIDDEN_MIGRATION_STATEMENTS:
            if statement in text:
                failures.append(f"{relative} 包含高危语句 {statement}")

        if "dba" in parts:
            # DBA-only scripts are executed manually, never auto-applied.
            warnings.append(f"{relative} 位于 dba 目录，不参与自动迁移")
            continue

        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            warnings.append(f"迁移文件命名不规范，未纳入版本校验: {relative}")
            continue

        dialect = path.parent.name.lower()
        seen.setdefault((dialect, match.group(1)), []).append(path)

    for (dialect, version), paths in seen.items():
        if len(paths) > 1:
            names = ", ".join(sorted(item.name for item in paths))
            failures.append(f"迁移版本号重复 {dialect}/V{version}: {names}")

    detail = f"扫描 {scanned} 个迁移文件，{sum(len(v) for v in seen.values())} 个纳入版本校验"
    if failures:
        return GateResult("迁移静态检查", False, "; ".join(failures), warnings)
    return GateResult("迁移静态检查", True, detail, warnings)


def check_secrets() -> GateResult:
    """Fail on tracked secrets, warn on anything that merely looks like one."""
    failures: list[str] = []
    warnings: list[str] = []

    code, output = _run(["git", "ls-files"])
    tracked = output.splitlines() if code == 0 else []
    for name in tracked:
        if name.strip() == ".env" or name.strip().endswith("/.env"):
            failures.append(f"环境文件已被 Git 跟踪: {name}")
        if name.strip().endswith((".pem", ".key", ".p12")):
            failures.append(f"私钥文件已被 Git 跟踪: {name}")

    env_example = PROJECT_ROOT / ".env.example"
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        if any(part in SCAN_SKIP_DIRS for part in path.parts):
            continue
        if path == env_example:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                snippet = match.group(0)
                lowered = snippet.lower()
                if any(token in lowered for token in SECRET_ALLOWLIST):
                    continue
                if _looks_like_code_identifier(snippet):
                    # Vue/JSX attributes such as password="changePassword".
                    continue
                relative = path.relative_to(PROJECT_ROOT)
                warnings.append(f"{relative}: 疑似硬编码凭据 ({snippet[:40]}…)")

    if failures:
        return GateResult("密钥扫描", False, "; ".join(failures), warnings)
    detail = "未发现被跟踪的环境文件或私钥"
    return GateResult("密钥扫描", True, detail, warnings)


def _compose_service(text: str, service: str) -> str:
    """Return one top-level Compose service body without requiring PyYAML."""
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\s*\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\s*\n|^volumes:\s*$|\Z)",
        text,
    )
    return match.group("body") if match else ""


def check_supply_chain_config() -> GateResult:
    """Block dependency drift and missing baseline container hardening."""
    failures: list[str] = []
    warnings: list[str] = []

    package_path = PROJECT_ROOT / "frontend" / "package.json"
    lock_path = PROJECT_ROOT / "frontend" / "package-lock.json"
    if not package_path.exists():
        failures.append("frontend/package.json 缺失")
    else:
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"frontend/package.json 无法解析: {exc}")
        else:
            declared: dict[str, str] = {}
            for section in ("dependencies", "devDependencies"):
                declared.update(package.get(section, {}))
            unstable = [
                f"{name}={version}"
                for name, version in declared.items()
                if not isinstance(version, str)
                or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version)
            ]
            if unstable:
                failures.append("前端依赖必须使用精确版本: " + ", ".join(unstable))

            if not lock_path.exists():
                failures.append("frontend/package-lock.json 缺失")
            else:
                try:
                    lock = json.loads(lock_path.read_text(encoding="utf-8"))
                    root = lock.get("packages", {}).get("", {})
                    locked = {
                        **root.get("dependencies", {}),
                        **root.get("devDependencies", {}),
                    }
                    mismatched = [
                        name for name, version in declared.items() if locked.get(name) != version
                    ]
                    if mismatched:
                        failures.append(
                            "package.json 与 lockfile 根依赖不一致: " + ", ".join(mismatched)
                        )
                except (OSError, json.JSONDecodeError, AttributeError) as exc:
                    failures.append(f"frontend/package-lock.json 无法解析: {exc}")

    dockerfile = PROJECT_ROOT / "Dockerfile"
    if dockerfile.exists():
        docker_text = dockerfile.read_text(encoding="utf-8", errors="replace")
        users = re.findall(r"(?im)^\s*USER\s+([^\s#]+)", docker_text)
        if not users or users[-1].lower() in {"root", "0"}:
            failures.append("Dockerfile 必须以显式非 root USER 运行")
        for image in re.findall(r"(?im)^\s*FROM\s+([^\s]+)", docker_text):
            if "@sha256:" not in image:
                warnings.append(f"基础镜像未固定 digest: {image}")
    else:
        failures.append("Dockerfile 缺失")

    dockerignore = PROJECT_ROOT / ".dockerignore"
    if not dockerignore.exists():
        failures.append(".dockerignore 缺失")
    else:
        ignored = {
            line.strip()
            for line in dockerignore.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        required_ignores = {".git", ".env", ".env.*", ".venv", "artifacts", "tests"}
        missing_ignores = sorted(required_ignores - ignored)
        if missing_ignores:
            failures.append(".dockerignore 缺少: " + ", ".join(missing_ignores))

    # Operational scripts must never silently fall back to a database password.
    secret_default_files = (
        PROJECT_ROOT / "scripts" / "backup_mysql.cmd",
        PROJECT_ROOT / "scripts" / "restore_mysql.cmd",
        PROJECT_ROOT / "scripts" / "load_enterprise_mysql.py",
    )
    for path in secret_default_files:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if re.search(
            r"(?im)\bset\s+MYSQL(?:_ROOT)?_PASSWORD\s*=\s*(?!%)[^\s\r\n]+",
            content,
        ) or re.search(
            r"os\.getenv\(\s*[\"']MYSQL(?:_ROOT)?_PASSWORD[\"']\s*,\s*[\"'][^\"']+[\"']\s*\)",
            content,
        ):
            failures.append(f"{path.relative_to(PROJECT_ROOT)} 不得包含数据库密码默认值")
        if re.search(r"-p%MYSQL(?:_ROOT)?_PASSWORD%", content, flags=re.IGNORECASE):
            failures.append(f"{path.relative_to(PROJECT_ROOT)} 不得将数据库密码拼入命令参数")

    compose_path = PROJECT_ROOT / "docker-compose.app.yml"
    if compose_path.exists():
        compose = compose_path.read_text(encoding="utf-8", errors="replace")
        redis_body = _compose_service(compose, "redis")
        if not redis_body:
            failures.append("生产 Compose 缺少 redis 服务")
        elif not re.search(r"(?m)^    healthcheck:\s*$", redis_body):
            failures.append("生产 Compose 的 redis 服务缺少 healthcheck")
        for service in ("api", "export-worker"):
            body = _compose_service(compose, service)
            if not body:
                failures.append(f"生产 Compose 缺少 {service} 服务")
                continue
            if not re.search(
                r"(?ms)^    depends_on:\s*$.*?^      redis:\s*$\n"
                r"        condition:\s*service_healthy\s*$",
                body,
            ):
                failures.append(f"{service} 必须等待 redis service_healthy")
            if not re.search(r"(?m)^    security_opt:\s*$", body):
                failures.append(f"{service} 必须启用 security_opt")
            if not re.search(r"(?m)^      - no-new-privileges:true\s*$", body):
                failures.append(f"{service} 必须启用 no-new-privileges")
            if not re.search(r"(?m)^    cap_drop:\s*$", body) or not re.search(
                r"(?m)^      - ALL\s*$", body
            ):
                failures.append(f"{service} 必须 drop 全部 Linux capabilities")
            if not re.search(r"(?m)^    pids_limit:\s*[1-9]\d*\s*$", body):
                failures.append(f"{service} 必须设置 pids_limit")
            if not re.search(r"(?m)^    mem_limit:\s*\S+\s*$", body):
                failures.append(f"{service} 必须设置 mem_limit")
        for service in ("mysql", "redis"):
            body = _compose_service(compose, service)
            image_match = re.search(r"(?m)^    image:\s*([^\s]+)\s*$", body)
            if not image_match or not re.search(r"@sha256:[0-9a-f]{64}$", image_match.group(1)):
                failures.append(f"{service} 镜像必须固定 sha256 digest")
    else:
        failures.append("docker-compose.app.yml 缺失")

    local_compose_path = PROJECT_ROOT / "docker-compose.mysql.yml"
    if local_compose_path.exists():
        local_compose = local_compose_path.read_text(encoding="utf-8", errors="replace")
        mysql_body = _compose_service(local_compose, "mysql")
        image_match = re.search(r"(?m)^    image:\s*([^\s]+)\s*$", mysql_body)
        if not image_match or not re.search(r"@sha256:[0-9a-f]{64}$", image_match.group(1)):
            failures.append("本地 MySQL Compose 镜像必须固定 sha256 digest")
        required_secret_references = {
            "MYSQL_PASSWORD": "MYSQL_PASSWORD",
            "MYSQL_ROOT_PASSWORD": "MYSQL_ROOT_PASSWORD",
            "MYSQL_READONLY_PASSWORD": "MYSQL_QUERY_PASSWORD",
        }
        for compose_key, env_key in required_secret_references.items():
            if not re.search(
                rf"(?m)^      {compose_key}:\s*\$\{{{env_key}:\?[^}}]+\}}\s*$",
                mysql_body,
            ):
                failures.append(f"本地 MySQL Compose 的 {compose_key} 必须通过 {env_key} 环境变量注入")
    else:
        failures.append("docker-compose.mysql.yml 缺失")

    if failures:
        return GateResult("供应链与容器静态检查", False, "; ".join(failures), warnings)
    return GateResult(
        "供应链与容器静态检查",
        True,
        "前端依赖已精确锁定，容器基线已加固，本地数据库凭据仅通过环境变量注入",
        warnings,
    )


def _parse_pip_audit(output: str) -> int | None:
    """Return vulnerability count across pip-audit JSON output versions.

    pip-audit 2.x has emitted both a bare dependency list and an object with a
    ``dependencies`` field. Windows virtualenv warnings can precede the JSON,
    so decode from candidate JSON starts instead of requiring a clean stdout.
    """
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", output):
        try:
            payload, _ = decoder.raw_decode(output[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            dependencies = payload
        elif isinstance(payload, dict) and isinstance(payload.get("dependencies"), list):
            dependencies = payload["dependencies"]
        else:
            continue
        if not all(isinstance(item, dict) and isinstance(item.get("vulns", []), list) for item in dependencies):
            continue
        return sum(len(item["vulns"]) for item in dependencies)
    return None


def _parse_npm_audit(output: str) -> int | None:
    try:
        payload = json.loads(output)
        total = payload.get("metadata", {}).get("vulnerabilities", {}).get("total")
        return int(total) if total is not None else None
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return None


def check_dependency_vulnerabilities(*, require: bool = False) -> GateResult:
    """Run available scanners; operational skips are explicit, never false green.

    ``require`` is intended for release jobs. Local/offline checks may keep the
    warning-only behavior, while a release job must fail if either scanner did
    not actually run to completion.
    """
    details: list[str] = []
    warnings: list[str] = []
    findings: list[str] = []
    incomplete = 0

    pip_audit = shutil.which("pip-audit")
    if pip_audit:
        code, output = _run([pip_audit, "-r", "requirements.txt", "-f", "json"], timeout=60)
        count = _parse_pip_audit(output)
        if count is None:
            incomplete += 1
            details.append("Python：未执行完成")
            warnings.append(f"pip-audit 不可用或扫描失败（退出码 {code}）")
        elif count:
            findings.append(f"Python 依赖发现 {count} 个漏洞记录")
        else:
            details.append("Python：实际扫描，未发现已知漏洞")
    elif importlib.util.find_spec("pip_audit") is not None:
        code, output = _run(
            [sys.executable, "-m", "pip_audit", "-r", "requirements.txt", "-f", "json"],
            timeout=60,
        )
        count = _parse_pip_audit(output)
        if count is None:
            incomplete += 1
            details.append("Python：未执行完成")
            warnings.append(f"pip-audit 扫描失败（退出码 {code}）")
        elif count:
            findings.append(f"Python 依赖发现 {count} 个漏洞记录")
        else:
            details.append("Python：实际扫描，未发现已知漏洞")
    else:
        incomplete += 1
        details.append("Python：未执行（pip-audit 未安装）")
        warnings.append("Python 依赖漏洞扫描未执行")

    frontend = PROJECT_ROOT / "frontend"
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if npm and (frontend / "package-lock.json").exists():
        audit_command = [npm, "audit", "--omit=dev", "--json"]
        code, output = _run(audit_command, cwd=frontend, timeout=60)
        count = _parse_npm_audit(output)
        if count is None:
            incomplete += 1
            details.append("前端：未执行完成")
            warnings.append(f"npm audit 不可用或扫描失败（退出码 {code}）；请检查 registry 网络和证书")
        elif count:
            findings.append(f"前端生产依赖发现 {count} 个漏洞")
        else:
            details.append("前端：实际扫描，未发现已知漏洞")
    else:
        incomplete += 1
        details.append("前端：未执行（npm 或 lockfile 不可用）")
        warnings.append("前端依赖漏洞扫描未执行")

    if findings or (require and incomplete):
        if require and incomplete:
            findings.append(f"有 {incomplete} 项依赖漏洞扫描未实际完成")
        return GateResult(
            "依赖漏洞扫描",
            False,
            "; ".join(findings + details),
            warnings,
            executed=incomplete == 0,
            blocking=bool(findings) or require,
        )
    return GateResult(
        "依赖漏洞扫描",
        True,
        "; ".join(details),
        warnings,
        executed=incomplete == 0,
        blocking=require,
    )


def check_backend_tests(extra_args: list[str] | None = None) -> GateResult:
    command = [sys.executable, "-m", "pytest", "-q", *(extra_args or [])]
    code, output = _run(command)
    return GateResult("后端测试", code == 0, _pytest_summary(output))


def check_high_risk_regression() -> GateResult:
    """The suites that must never regress: export, auth, guardrails, policy."""
    targets = [
        "tests/test_auth_api.py",
        "tests/test_export_queue.py",
        "tests/test_guardrails.py",
        "tests/test_policy.py",
        "tests/test_api.py",
    ]
    existing = [name for name in targets if (PROJECT_ROOT / name).exists()]
    if not existing:
        return GateResult("高风险回归", True, "未找到指定回归用例，跳过")
    code, output = _run([sys.executable, "-m", "pytest", "-q", *existing])
    return GateResult("高风险回归", code == 0, _pytest_summary(output))


def check_sbom() -> GateResult:
    script = PROJECT_ROOT / "scripts" / "generate_sbom.py"
    if not script.exists():
        return GateResult("SBOM 生成", False, "scripts/generate_sbom.py 缺失")
    code, output = _run(
        [sys.executable, str(script), "--output", "artifacts/sbom.cdx.json"], timeout=30
    )
    target = PROJECT_ROOT / "artifacts" / "sbom.cdx.json"
    if code != 0 or not target.exists():
        return GateResult("SBOM 生成", False, output or "SBOM 未生成")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        components = payload.get("components", [])
        valid = (
            payload.get("bomFormat") == "CycloneDX"
            and payload.get("specVersion") == "1.5"
            and isinstance(components, list)
            and bool(components)
        )
    except (OSError, json.JSONDecodeError, AttributeError):
        valid = False
        components = []
    return GateResult(
        "SBOM 生成",
        valid,
        f"CycloneDX 1.5，{len(components)} 个组件" if valid else "SBOM 格式无效",
    )


def check_frontend_build() -> GateResult:
    frontend = PROJECT_ROOT / "frontend"
    if not (frontend / "package.json").exists():
        return GateResult("前端构建", True, "未找到前端工程，跳过", executed=False)
    code, output = _run(["npm", "run", "build"], cwd=frontend)
    tail = output.splitlines()[-1] if output else "无输出"
    return GateResult("前端构建", code == 0, tail[:200])


def check_e2e() -> GateResult:
    """E2E needs a running backend; absence is reported, not silently green."""
    frontend = PROJECT_ROOT / "frontend"
    if not (frontend / "package.json").exists():
        return GateResult("E2E", True, "未找到前端工程，跳过", executed=False, blocking=False)
    code, output = _run(["npx", "playwright", "test", "--reporter=line"], cwd=frontend)
    if code == 127:
        return GateResult(
            "E2E",
            True,
            "Playwright 不可用，跳过（需在集成环境执行）",
            executed=False,
            blocking=False,
        )
    tail = output.splitlines()[-1] if output else "无输出"
    return GateResult("E2E", code == 0, tail[:200])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query Pilot merge gate")
    parser.add_argument("--skip-frontend", action="store_true", help="跳过前端构建")
    parser.add_argument("--skip-e2e", action="store_true", help="跳过 E2E")
    parser.add_argument("--skip-tests", action="store_true", help="跳过测试，只做静态检查")
    parser.add_argument("--skip-audit", action="store_true", help="跳过依赖漏洞扫描")
    parser.add_argument("--require-audit", action="store_true", help="要求依赖漏洞扫描实际完成，否则失败")
    parser.add_argument("--report", default="", help="写入 JSON 报告的路径")
    args = parser.parse_args(argv)

    results: list[GateResult] = [
        check_migrations(),
        check_secrets(),
        check_supply_chain_config(),
    ]

    if not args.skip_audit:
        results.append(check_dependency_vulnerabilities(require=args.require_audit))

    if not args.skip_tests:
        results.append(check_backend_tests())
        results.append(check_high_risk_regression())

    results.append(check_sbom())

    if not args.skip_frontend:
        results.append(check_frontend_build())

    if not args.skip_e2e:
        results.append(check_e2e())

    print("=" * 60)
    print("Query Pilot 合并门禁")
    print("=" * 60)
    for result in results:
        print(f"[{result.status}] {result.name}: {result.detail}")
        for warning in result.warnings[:5]:
            print(f"        警告: {warning}")
        if len(result.warnings) > 5:
            print(f"        … 另有 {len(result.warnings) - 5} 条警告")

    failed = [item for item in results if not item.passed]
    print("-" * 60)
    print(f"通过 {len(results) - len(failed)}/{len(results)} 项门禁")

    if args.report:
        report_path = PROJECT_ROOT / args.report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "gates": [asdict(item) for item in results],
            "failed": [item.name for item in failed],
            "not_executed": [item.name for item in results if not item.executed],
            "blocking_failures": [item.name for item in failed if item.blocking],
        }
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"报告已写入: {report_path.relative_to(PROJECT_ROOT)}")

    if failed:
        print(f"合并门禁未通过: {', '.join(item.name for item in failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
