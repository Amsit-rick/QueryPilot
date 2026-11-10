r"""Seed deterministic multi-month analytics data into local demo databases.

This script is intentionally limited to reserved IDs starting at 910000. It
does not delete or rewrite the original demo rows and is safe to run repeatedly.

Windows CMD examples from the project root:

    .venv\Scripts\python.exe scripts\seed_multimonth_demo_data.py ^
      --postgres-container query-pilot-postgres-postgres-1

    .venv\Scripts\python.exe scripts\seed_multimonth_demo_data.py ^
      --oracle-sysdba --oracle-pdb ORCLPDB1
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")
_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_REGIONS = ("华东", "华南", "华北", "西南")
_WEIGHTS = (Decimal("0.34"), Decimal("0.26"), Decimal("0.22"), Decimal("0.18"))


@dataclass(frozen=True)
class DemoSale:
    id: int
    customer_id: int
    region: str
    amount: Decimal
    sold_at: date


def _month_shift(value: date, offset: int) -> date:
    absolute = value.year * 12 + value.month - 1 + offset
    return date(absolute // 12, absolute % 12 + 1, 1)


def build_demo_rows(*, end_month: date, months: int = 12) -> list[DemoSale]:
    if end_month.day != 1:
        raise ValueError("end month must be the first day of a month")
    if not 4 <= months <= 36:
        raise ValueError("months must be between 4 and 36")
    start = _month_shift(end_month, -(months - 1))
    monthly_totals = [Decimal(7200 + index * 550) for index in range(months)]
    monthly_totals[max(0, months - 2)] *= Decimal("1.85")
    rows: list[DemoSale] = []
    for month_index, total in enumerate(monthly_totals):
        month = _month_shift(start, month_index)
        allocated = Decimal("0")
        for region_index, (region, weight) in enumerate(zip(_REGIONS, _WEIGHTS)):
            amount = (total - allocated) if region_index == len(_REGIONS) - 1 else (total * weight).quantize(Decimal("0.01"))
            allocated += amount
            rows.append(
                DemoSale(
                    id=910000 + month_index * 10 + region_index + 1,
                    customer_id=1 if region_index % 2 == 0 else 2,
                    region=region,
                    amount=amount,
                    sold_at=date(month.year, month.month, 5 + region_index * 5),
                )
            )
    return rows


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_postgres_upsert(rows: list[DemoSale]) -> str:
    if not rows:
        raise ValueError("demo rows cannot be empty")
    values = ",\n    ".join(
        f"({row.id}, {_literal(row.region)}, {row.amount}, DATE '{row.sold_at.isoformat()}')"
        for row in rows
    )
    return (
        "INSERT INTO sales (id, region, amount, sold_at) VALUES\n    "
        + values
        + "\nON CONFLICT (id) DO UPDATE SET "
        "region=EXCLUDED.region, amount=EXCLUDED.amount, sold_at=EXCLUDED.sold_at;"
    )


def _validated_identifier(value: str, label: str) -> str:
    normalized = str(value or "").strip().upper()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{label} must be a simple SQL identifier")
    return normalized


def build_oracle_merge(rows: list[DemoSale], *, schema_owner: str) -> str:
    owner = _validated_identifier(schema_owner, "schema owner")
    if not rows:
        raise ValueError("demo rows cannot be empty")
    source = "\nUNION ALL\n".join(
        "SELECT "
        f"{row.id} AS id, {row.customer_id} AS customer_id, {_literal(row.region)} AS region, "
        f"{row.amount} AS amount, TIMESTAMP '{row.sold_at.isoformat()} 10:00:00' AS created_at FROM dual"
        for row in rows
    )
    return f"""MERGE INTO {owner}.sales target
USING (
{source}
) source
ON (target.id = source.id)
WHEN MATCHED THEN UPDATE SET
    target.customer_id = source.customer_id,
    target.region = source.region,
    target.amount = source.amount,
    target.created_at = source.created_at
WHEN NOT MATCHED THEN INSERT (id, customer_id, region, amount, created_at)
VALUES (source.id, source.customer_id, source.region, source.amount, source.created_at)"""


def _parse_month(value: str) -> date:
    try:
        year, month = (int(part) for part in value.split("-", 1))
        return date(year, month, 1)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("month must use YYYY-MM") from exc


def _run_postgres(container: str, sql: str) -> None:
    if not _CONTAINER.fullmatch(container):
        raise ValueError("PostgreSQL container must be a simple Docker container name")
    subprocess.run(
        ["docker", "exec", container, "psql", "-U", "query_app", "-d", "query_agent", "-v", "ON_ERROR_STOP=1", "-c", sql],
        check=True,
        capture_output=True,
        text=True,
    )


def _wait_for_postgres(container: str, *, wait_seconds: int) -> None:
    if not _CONTAINER.fullmatch(container):
        raise ValueError("PostgreSQL container must be a simple Docker container name")
    if not 1 <= wait_seconds <= 300:
        raise ValueError("PostgreSQL wait seconds must be between 1 and 300")
    deadline = time.monotonic() + wait_seconds
    command = ["docker", "exec", container, "pg_isready", "-U", "query_app", "-d", "query_agent"]
    while True:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"PostgreSQL did not become ready within {wait_seconds} seconds")
        time.sleep(1)


def _run_oracle_sysdba(*, sqlplus: Path, pdb: str, merge_sql: str) -> None:
    pdb_name = _validated_identifier(pdb, "Oracle PDB")
    if not sqlplus.is_file():
        raise FileNotFoundError(f"sqlplus not found: {sqlplus}")
    script = f"""WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET DEFINE OFF HEADING OFF FEEDBACK OFF PAGESIZE 0
ALTER SESSION SET CONTAINER = {pdb_name};
{merge_sql};
COMMIT;
EXIT SUCCESS
"""
    subprocess.run(
        [str(sqlplus), "-S", "/ as sysdba"],
        input=script,
        check=True,
        capture_output=True,
        text=True,
    )


def _resolve_oracle_home(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    configured = os.getenv("ORACLE_HOME", "").strip()
    if not configured:
        raise ValueError("Oracle seeding requires --oracle-home or ORACLE_HOME")
    return Path(configured)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end-month", type=_parse_month, default=date.today().replace(day=1))
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--postgres-container")
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--oracle-sysdba", action="store_true")
    parser.add_argument("--oracle-pdb", default="ORCLPDB1")
    parser.add_argument("--oracle-schema-owner", default="QUERYPILOT_OWNER")
    parser.add_argument(
        "--oracle-home",
        type=Path,
        default=None,
    )
    args = parser.parse_args(argv)
    if not args.postgres_container and not args.oracle_sysdba:
        parser.error("select at least one target")
    try:
        rows = build_demo_rows(end_month=args.end_month, months=args.months)
        if args.postgres_container:
            if args.wait_seconds:
                _wait_for_postgres(args.postgres_container, wait_seconds=args.wait_seconds)
            _run_postgres(args.postgres_container, build_postgres_upsert(rows))
            print(f"PostgreSQL demo data seeded: rows={len(rows)} months={args.months}")
        if args.oracle_sysdba:
            oracle_home = _resolve_oracle_home(args.oracle_home)
            _run_oracle_sysdba(
                sqlplus=oracle_home / "bin" / "sqlplus.exe",
                pdb=args.oracle_pdb,
                merge_sql=build_oracle_merge(rows, schema_owner=args.oracle_schema_owner),
            )
            print(f"Oracle demo data seeded: rows={len(rows)} months={args.months}")
    except Exception as exc:
        print(f"Demo data seeding failed: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
