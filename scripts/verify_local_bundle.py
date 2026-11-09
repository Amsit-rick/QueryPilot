"""Read-only checks for a Query Pilot local delivery bundle.

The checker is intentionally independent from the application settings loader so
it can explain a broken first-run environment before importing the application.
It never prints environment values; only the presence and safe shape of keys are
reported.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    ".env",
    ".venv/Scripts/python.exe",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/node_modules",
    "docker-compose.mysql.yml",
    "migrations/mysql",
    "scripts/start_local.cmd",
)
REQUIRED_ENV_KEYS = (
    "DATABASE_BACKEND",
    "MYSQL_HOST",
    "MYSQL_PORT",
    "MYSQL_DATABASE",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "MYSQL_QUERY_USER",
    "MYSQL_QUERY_PASSWORD",
)
PORTS = {"mysql": 3307, "api": 8000, "frontend": 5173}
_ENV_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_ASSIGNMENT.match(line)
        if not match or match.group(1).startswith("#"):
            continue
        value = match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def _port_open(port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def _add(report: dict, bucket: str, message: str) -> None:
    report[bucket].append(message)


def _major_version(output: str) -> int | None:
    match = re.search(r"(?:v)?(\d+)(?:\.\d+)?(?:\.\d+)?", output or "")
    return int(match.group(1)) if match else None


def verify_bundle(
    root: Path = ROOT,
    *,
    runner: Callable[[list[str], Path], subprocess.CompletedProcess[str]] = _run,
    port_checker: Callable[[int], bool] = _port_open,
    which: Callable[[str], str | None] = shutil.which,
) -> dict:
    """Return a JSON-safe preflight report without modifying the machine."""
    root = root.resolve()
    report: dict = {
        "format": "query-pilot-local-bundle-preflight",
        "version": 1,
        "root": str(root),
        "passed": [],
        "warnings": [],
        "blocking": [],
        "checks": [],
        "secrets_redacted": True,
    }

    def check(name: str, status: str, detail: str) -> None:
        report["checks"].append({"name": name, "status": status, "detail": detail})
        if status == "passed":
            _add(report, "passed", detail)
        elif status == "warning":
            _add(report, "warnings", detail)
        else:
            _add(report, "blocking", detail)

    for relative in REQUIRED_FILES:
        path = root / relative
        if path.exists():
            check(f"file:{relative}", "passed", f"文件存在: {relative}")
        else:
            check(f"file:{relative}", "blocking", f"缺少必需文件或目录: {relative}")

    env_path = root / ".env"
    env = _read_env(env_path)
    if env_path.is_file():
        check("env-readable", "passed", ".env 已存在且可读取")
        for key in REQUIRED_ENV_KEYS:
            if env.get(key, "").strip():
                check(f"env:{key}", "passed", f"已配置 {key}")
            elif key in {"MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "MYSQL_QUERY_PASSWORD"}:
                check(f"env:{key}", "blocking", f"{key} 未配置，不能启动本地 MySQL")
            else:
                check(f"env:{key}", "blocking", f"{key} 未配置")
        if env.get("DATABASE_BACKEND", "").strip().lower() != "mysql":
            check("env:database-backend", "blocking", "DATABASE_BACKEND 必须为 mysql")
        try:
            port = int(env.get("MYSQL_PORT", ""))
        except ValueError:
            port = 0
        if not 1 <= port <= 65535:
            check("env:mysql-port", "blocking", "MYSQL_PORT 必须是 1-65535 的端口号")
        database_passwords = [
            env.get(key, "").strip()
            for key in ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "MYSQL_QUERY_PASSWORD")
            if env.get(key, "").strip()
        ]
        if len(database_passwords) == 3 and len(set(database_passwords)) < 3:
            check("env:mysql-password-separation", "warning", "本地 MySQL 账号仍复用密码；建议重新生成并按账号轮换")
        elif len(database_passwords) == 3:
            check("env:mysql-password-separation", "passed", "本地 MySQL root、控制面和只读查询密码已分离")
    else:
        check("env-readable", "blocking", "缺少 .env；请先运行 scripts\\setup_local.cmd")

    python_path = root / ".venv/Scripts/python.exe"
    if python_path.is_file():
        if sys.version_info >= (3, 12):
            check("python-version", "passed", "当前预检 Python 版本满足 3.12+")
        else:
            check("python-version", "blocking", "当前预检 Python 版本低于 3.12；请使用 .venv\\Scripts\\python.exe")
    else:
        check("python-version", "blocking", "虚拟环境 Python 不存在；请先运行 scripts\\setup_local.cmd")

    for command, label, minimum in (("node", "Node.js", 22), ("npm", "npm", 10)):
        executable = which(command) or (which("npm.cmd") if command == "npm" else None)
        if not executable:
            check(f"tool:{command}", "blocking", f"{label} 不可用")
            continue
        version = runner([executable, "--version"], root)
        major = _major_version(version.stdout)
        if version.returncode != 0 or major is None:
            check(f"tool:{command}", "blocking", f"{label} 无法读取版本")
        elif major < minimum:
            check(f"tool:{command}", "blocking", f"{label} 版本过低，需要 {minimum}+ 主版本")
        else:
            check(f"tool:{command}", "passed", f"{label} 版本满足要求（{major}.x）")

    if which("docker"):
        check("tool:docker", "passed", "Docker CLI 可用")
    else:
        check("tool:docker", "blocking", "Docker CLI 不可用")

    if which("docker"):
        docker_info = runner(["docker", "info"], root)
        if docker_info.returncode == 0:
            check("docker-daemon", "passed", "Docker Desktop 守护进程可用")
            compose = runner(["docker", "compose", "-f", "docker-compose.mysql.yml", "config", "--quiet"], root)
            if compose.returncode == 0:
                check("compose-config", "passed", "MySQL Compose 配置可解析")
            else:
                check("compose-config", "blocking", "MySQL Compose 配置无法解析")
        else:
            check("docker-daemon", "blocking", "Docker Desktop 未运行或当前用户无权访问")
    else:
        check("docker-daemon", "blocking", "Docker CLI 不可用，无法检查 Docker Desktop")

    if port_checker(PORTS["mysql"]):
        check("port:mysql", "passed", "MySQL 端口 3307 正在监听")
    else:
        check("port:mysql", "blocking", "MySQL 端口 3307 未监听；请确认 Docker MySQL 已启动")
    for name, port in (("api", PORTS["api"]), ("frontend", PORTS["frontend"])):
        if port_checker(port):
            check(f"port:{name}", "blocking", f"端口 {port} 已被占用，请先关闭旧的 {name} 服务")
        else:
            check(f"port:{name}", "passed", f"端口 {port} 可用于启动 {name}")

    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only local Query Pilot bundle preflight")
    parser.add_argument("--root", type=Path, default=ROOT, help="project root (default: repository root)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = verify_bundle(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
