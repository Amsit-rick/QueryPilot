ALTER TABLE semantic_entries
  ADD CONSTRAINT uq_semantic_version UNIQUE (tenant_id, logical_id, version);
