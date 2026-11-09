CREATE TABLE IF NOT EXISTS semantic_entries (
  id CHAR(36) PRIMARY KEY,
  logical_id CHAR(36) NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  datasource_id VARCHAR(128) NOT NULL,
  kind VARCHAR(32) NOT NULL,
  name VARCHAR(255) NOT NULL,
  description TEXT NULL,
  definition_json JSON NOT NULL,
  version INT NOT NULL,
  previous_id CHAR(36) NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  owner_id BIGINT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_semantic_current (tenant_id, datasource_id, logical_id, version),
  INDEX idx_semantic_search (tenant_id, datasource_id, kind, active),
  CONSTRAINT fk_semantic_owner FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT fk_semantic_previous FOREIGN KEY (previous_id) REFERENCES semantic_entries(id) ON DELETE SET NULL
) CHARACTER SET utf8mb4;
