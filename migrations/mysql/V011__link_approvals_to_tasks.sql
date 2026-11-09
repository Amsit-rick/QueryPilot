ALTER TABLE query_approvals
  ADD COLUMN task_id CHAR(36) NULL AFTER requester_id,
  ADD INDEX idx_query_approvals_task (task_id),
  ADD CONSTRAINT fk_query_approvals_task FOREIGN KEY (task_id) REFERENCES data_tasks(id) ON DELETE SET NULL;
