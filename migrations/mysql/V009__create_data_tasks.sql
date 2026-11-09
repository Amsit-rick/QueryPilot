CREATE TABLE IF NOT EXISTS data_tasks (
  id CHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  owner_id VARCHAR(64) NOT NULL,
  datasource_id VARCHAR(128) NOT NULL,
  title VARCHAR(255) NOT NULL,
  question TEXT NOT NULL,
  status VARCHAR(32) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_data_tasks_owner (tenant_id, owner_id, updated_at),
  INDEX idx_data_tasks_status (tenant_id, status, updated_at)
) CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS data_task_steps (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id CHAR(36) NOT NULL,
  sequence_no INT NOT NULL,
  kind VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  input_json JSON NULL,
  output_json JSON NULL,
  error_text TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_task_steps_task FOREIGN KEY (task_id) REFERENCES data_tasks(id) ON DELETE CASCADE,
  UNIQUE KEY uq_task_step_sequence (task_id, sequence_no),
  INDEX idx_task_steps_task (task_id, sequence_no)
) CHARACTER SET utf8mb4;
