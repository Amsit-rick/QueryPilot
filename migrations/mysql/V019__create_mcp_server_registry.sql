CREATE TABLE IF NOT EXISTS mcp_server_registry (
  id CHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  name VARCHAR(120) NOT NULL,
  transport VARCHAR(32) NOT NULL,
  endpoint VARCHAR(500) NOT NULL,
  auth_env_ref VARCHAR(128) NOT NULL,
  allowed_tools_json TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  read_only_only BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_mcp_server_name (tenant_id, name),
  INDEX idx_mcp_server_enabled (tenant_id, enabled)
) CHARACTER SET utf8mb4;

