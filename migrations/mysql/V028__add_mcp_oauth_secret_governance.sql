ALTER TABLE mcp_server_registry
  ADD COLUMN auth_type VARCHAR(32) NOT NULL DEFAULT 'bearer' AFTER auth_env_ref,
  ADD COLUMN oauth_token_url VARCHAR(500) NULL AFTER auth_type,
  ADD COLUMN oauth_client_id VARCHAR(200) NULL AFTER oauth_token_url,
  ADD COLUMN oauth_scopes_json TEXT NULL AFTER oauth_client_id,
  ADD COLUMN credential_version INT NOT NULL DEFAULT 1 AFTER oauth_scopes_json,
  ADD COLUMN credential_rotated_at DATETIME NULL AFTER credential_version;
