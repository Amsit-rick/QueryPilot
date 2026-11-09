CREATE TABLE IF NOT EXISTS query_approvals (
  id CHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(128) NOT NULL,
  requester_id BIGINT NULL,
  question VARCHAR(2000) NOT NULL,
  sql_text TEXT NOT NULL,
  params_json JSON NOT NULL,
  datasource_id VARCHAR(128) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL,
  INDEX idx_query_approvals_tenant (tenant_id, status, created_at),
  INDEX idx_query_approvals_expiry (expires_at),
  CONSTRAINT fk_query_approvals_user FOREIGN KEY (requester_id) REFERENCES users(id) ON DELETE SET NULL
) CHARACTER SET utf8mb4;
