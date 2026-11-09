ALTER TABLE query_approvals
  ADD COLUMN reasons_json JSON NULL AFTER expires_at,
  ADD COLUMN trigger_type VARCHAR(32) NOT NULL DEFAULT 'manual' AFTER reasons_json,
  ADD COLUMN analysis_mode VARCHAR(32) NOT NULL DEFAULT 'none' AFTER trigger_type;
