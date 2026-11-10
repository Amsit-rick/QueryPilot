# Multi-dialect monthly trend guardrail TDD evidence

Date: 2026-11-10

## Defect

The same monthly sales trend question succeeded on MySQL but was rejected as
`E_READONLY_VIOLATION` on SQLite, PostgreSQL, Oracle, SQL Server and ClickHouse.
The security boundary only mapped three SQLGlot dialects, omitted pure monthly
date functions, and translated remote SQL through SQLite before execution.

## RED

- Added native monthly aggregation cases for SQLite `strftime`, PostgreSQL
  `date_trunc`, Oracle `TO_CHAR/TRUNC`, SQL Server `DATEFROMPARTS`, and
  ClickHouse `toStartOfMonth`.
- Added remote Gateway compilation cases that require native AST parsing.
- Initial focused run: `8 failed, 5 passed`; every failure was caused by the
  missing function allowlist or SQLite intermediate-dialect path.
- The real Oracle model output exposed a second RED case: SQLGlot normalizes
  Oracle `TRUNC(date, 'MM')` to `DATE_TRUNC`, which was still denied.

## GREEN

- Added native dialect mappings for Oracle, T-SQL and ClickHouse.
- Added only pure date conversion/bucketing functions to the explicit function
  allowlist; unknown, file, delay and extension functions remain denied.
- Removed SQLite normalization from runtime and offline connector paths.
- Remote Gateways now reject unsafe structure before opening a schema
  connection, then validate tables and columns using the native dialect.
- RBAC policy parsing now receives the same datasource dialect.
- Oracle `FETCH FIRST ... ROWS ONLY` remains bounded without accepting
  `PERCENT` or `WITH TIES` variants.

## Data contract and acceptance

- SQL Server provisioning now adds and backfills `sales.sold_at` idempotently.
- Added idempotent ClickHouse demo provisioning with three monthly buckets.
- The all-datasource launcher waits for ClickHouse readiness before migration.
- The unified acceptance probe now requires a date column and executes a real
  monthly aggregation in every configured dialect.

## Verification

- Focused guardrail/Gateway tests: `13 passed` after the first fix; Oracle
  `TRUNC` follow-up: `7 passed`.
- SQL security corpus: `33/33`, including native monthly queries and dangerous
  function regressions.
- Relevant regression selection: `181 passed`.
- Full backend: `1150 passed, 33 skipped, 1 warning`.
- Frontend production build: passed, 84 modules transformed.
- Python compile check: passed.
- Real datasource acceptance: all six datasources reported `READY` for health,
  schema, detail query and monthly aggregation.
- The same natural-language question, `分析每月销售趋势并找出波动最大的月份`,
  executed successfully through the real application pipeline on SQLite,
  MySQL, PostgreSQL, Oracle, SQL Server and ClickHouse.

## Security boundary retained

This change does not allow arbitrary functions and does not treat prompts as a
security boundary. Database read-only accounts, schema authorization, named
parameter binding, query bounds and the SQLGlot AST checks remain mandatory.
