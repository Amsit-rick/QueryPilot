-- DBA template. Replace both placeholders with approved identifiers and a
-- Vault-managed credential before execution. This file is not auto-migrated.
CREATE USER IF NOT EXISTS '<readonly-user>'@'%' IDENTIFIED BY '<vault-managed-password>';
GRANT SELECT, SHOW VIEW ON `<application-database>`.* TO '<readonly-user>'@'%';
FLUSH PRIVILEGES;
