CREATE TABLE IF NOT EXISTS query_request_admissions (
  tenant_id VARCHAR(64) NOT NULL,
  owner_id VARCHAR(128) NOT NULL,
  operation VARCHAR(32) NOT NULL,
  client_request_id VARCHAR(128) NOT NULL,
  request_sha256 CHAR(64) NOT NULL,
  task_id CHAR(36) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'running',
  error_code VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (tenant_id, owner_id, operation, client_request_id),
  INDEX idx_query_request_admission_task (tenant_id, task_id),
  CONSTRAINT chk_query_request_admission_status CHECK (status IN ('running','completed','failed'))
) CHARACTER SET utf8mb4;

