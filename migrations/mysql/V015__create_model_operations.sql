CREATE TABLE IF NOT EXISTS model_providers (
  id CHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  name VARCHAR(120) NOT NULL,
  adapter VARCHAR(32) NOT NULL,
  base_url VARCHAR(500) NOT NULL,
  model_name VARCHAR(160) NOT NULL,
  api_key_env VARCHAR(128) NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  health_status VARCHAR(32) NOT NULL DEFAULT 'unchecked',
  last_latency_ms INT NOT NULL DEFAULT 0,
  last_error_code VARCHAR(64) NULL,
  last_checked_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_model_provider_name (tenant_id, name),
  INDEX idx_model_provider_default (tenant_id, enabled, is_default)
) CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS model_prompt_versions (
  id CHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  name VARCHAR(64) NOT NULL,
  system_prompt TEXT NOT NULL,
  version INT NOT NULL,
  owner_id VARCHAR(128) NULL,
  active BOOLEAN NOT NULL DEFAULT FALSE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_model_prompt_version (tenant_id, name, version),
  INDEX idx_model_prompt_active (tenant_id, name, active)
) CHARACTER SET utf8mb4;
