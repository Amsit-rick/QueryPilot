ALTER TABLE mcp_server_registry
  ADD COLUMN allow_private_network BOOLEAN NOT NULL DEFAULT FALSE AFTER read_only_only,
  ADD COLUMN request_timeout_seconds DOUBLE NOT NULL DEFAULT 10 AFTER allow_private_network,
  ADD COLUMN max_concurrency INT NOT NULL DEFAULT 4 AFTER request_timeout_seconds,
  ADD COLUMN daily_call_limit INT NOT NULL DEFAULT 1000 AFTER max_concurrency,
  ADD COLUMN circuit_failure_threshold INT NOT NULL DEFAULT 3 AFTER daily_call_limit,
  ADD COLUMN circuit_reset_seconds INT NOT NULL DEFAULT 30 AFTER circuit_failure_threshold,
  ADD COLUMN health_status VARCHAR(32) NOT NULL DEFAULT 'unchecked' AFTER circuit_reset_seconds,
  ADD COLUMN last_latency_ms INT NOT NULL DEFAULT 0 AFTER health_status,
  ADD COLUMN last_error_code VARCHAR(64) NULL AFTER last_latency_ms,
  ADD COLUMN discovered_tools_json TEXT NULL AFTER last_error_code,
  ADD COLUMN last_checked_at DATETIME NULL AFTER discovered_tools_json;
