ALTER TABLE data_tasks
  ADD COLUMN recovery_fencing_token BIGINT NOT NULL DEFAULT 0 AFTER status;

CREATE INDEX idx_data_tasks_recovery
  ON data_tasks (status, updated_at, recovery_fencing_token);
