CREATE TABLE IF NOT EXISTS export_jobs (
  id CHAR(36) PRIMARY KEY,
  user_id BIGINT NULL,
  tenant_id VARCHAR(128) NOT NULL,
  datasource_id VARCHAR(128) NOT NULL,
  status VARCHAR(32) NOT NULL,
  sql_text TEXT NOT NULL,
  params_json JSON NOT NULL,
  file_path VARCHAR(1024) NULL,
  row_count INT NULL,
  error_message TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL,
  INDEX idx_export_jobs_owner (tenant_id, user_id, created_at),
  INDEX idx_export_jobs_expiry (expires_at),
  CONSTRAINT fk_export_jobs_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) CHARACTER SET utf8mb4;
