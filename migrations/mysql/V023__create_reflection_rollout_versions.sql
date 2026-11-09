CREATE TABLE IF NOT EXISTS reflection_rollout_versions (
  id CHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  version VARCHAR(32) NOT NULL,
  policy_json TEXT NOT NULL,
  state VARCHAR(16) NOT NULL DEFAULT 'draft',
  canary_percent INT NOT NULL DEFAULT 0,
  created_by VARCHAR(128) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_reflection_rollout_version (tenant_id, version),
  INDEX idx_reflection_rollout_runtime (tenant_id, state),
  CONSTRAINT chk_reflection_rollout_state CHECK (state IN ('draft','canary','active','retired')),
  CONSTRAINT chk_reflection_rollout_percent CHECK (canary_percent BETWEEN 0 AND 100)
) CHARACTER SET utf8mb4;

