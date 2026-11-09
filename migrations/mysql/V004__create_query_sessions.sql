CREATE TABLE IF NOT EXISTS query_sessions (
  id CHAR(36) PRIMARY KEY,
  user_id BIGINT NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  title VARCHAR(255) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_query_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  INDEX idx_query_sessions_user (user_id, updated_at)
) CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS query_messages (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  session_id CHAR(36) NOT NULL,
  user_id BIGINT NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  question TEXT NOT NULL,
  sql_text TEXT NOT NULL,
  result_json JSON NULL,
  row_count INT NOT NULL DEFAULT 0,
  trace_id CHAR(36) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_query_messages_session FOREIGN KEY (session_id) REFERENCES query_sessions(id) ON DELETE CASCADE,
  INDEX idx_query_messages_session (session_id, created_at),
  INDEX idx_query_messages_user (user_id, created_at)
) CHARACTER SET utf8mb4;
