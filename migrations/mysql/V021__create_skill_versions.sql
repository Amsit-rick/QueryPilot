CREATE TABLE IF NOT EXISTS skill_versions (
  id CHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  skill_id VARCHAR(64) NOT NULL,
  version VARCHAR(32) NOT NULL,
  definition_json TEXT NOT NULL,
  state VARCHAR(16) NOT NULL DEFAULT 'draft',
  canary_percent INT NOT NULL DEFAULT 0,
  created_by VARCHAR(128) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_skill_version (tenant_id, skill_id, version),
  INDEX idx_skill_runtime (tenant_id, skill_id, state)
) CHARACTER SET utf8mb4;
