-- 用户-数据源权限关联：行级过滤授权
CREATE TABLE IF NOT EXISTS user_data_grants (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT NOT NULL,
  datasource_id VARCHAR(64) NOT NULL DEFAULT 'default',
  scope_column VARCHAR(64) NOT NULL,
  scope_value VARCHAR(255) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_user_grants_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  UNIQUE KEY uq_user_grants (user_id, datasource_id, scope_column, scope_value),
  INDEX idx_user_grants_user (user_id)
) CHARACTER SET utf8mb4;
