ALTER TABLE data_tasks
  ADD COLUMN parent_task_id CHAR(36) NULL AFTER datasource_id,
  ADD COLUMN root_task_id CHAR(36) NULL AFTER parent_task_id,
  ADD COLUMN branch_source_trace_id VARCHAR(128) NULL AFTER root_task_id,
  ADD COLUMN branch_label VARCHAR(120) NOT NULL DEFAULT '' AFTER branch_source_trace_id,
  ADD COLUMN branch_depth INT NOT NULL DEFAULT 0 AFTER branch_label;

CREATE INDEX idx_data_tasks_branch_parent
  ON data_tasks (tenant_id, owner_id, parent_task_id, created_at);

CREATE INDEX idx_data_tasks_branch_root
  ON data_tasks (tenant_id, owner_id, root_task_id, branch_depth, created_at);
