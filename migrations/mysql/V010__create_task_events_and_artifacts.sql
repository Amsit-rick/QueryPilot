ALTER TABLE data_task_steps
  ADD COLUMN duration_ms INT NOT NULL DEFAULT 0 AFTER error_text,
  ADD COLUMN retry_count INT NOT NULL DEFAULT 0 AFTER duration_ms;

CREATE TABLE IF NOT EXISTS data_task_events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id CHAR(36) NOT NULL,
  kind VARCHAR(64) NOT NULL,
  payload_json JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_task_events_task FOREIGN KEY (task_id) REFERENCES data_tasks(id) ON DELETE CASCADE,
  INDEX idx_task_events_task (task_id, id)
) CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS data_task_artifacts (
  id CHAR(36) PRIMARY KEY,
  task_id CHAR(36) NOT NULL,
  kind VARCHAR(64) NOT NULL,
  name VARCHAR(255) NOT NULL,
  uri VARCHAR(2048) NOT NULL DEFAULT '',
  metadata_json JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_task_artifacts_task FOREIGN KEY (task_id) REFERENCES data_tasks(id) ON DELETE CASCADE,
  INDEX idx_task_artifacts_task (task_id, created_at)
) CHARACTER SET utf8mb4;
