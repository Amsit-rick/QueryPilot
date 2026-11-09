-- 会话管理：记录登录设备信息与最近活动时间
ALTER TABLE user_sessions
  ADD COLUMN ip_address VARCHAR(64) NULL AFTER refresh_token_hash,
  ADD COLUMN user_agent VARCHAR(512) NULL AFTER ip_address,
  ADD COLUMN last_active_at DATETIME NULL AFTER user_agent;
