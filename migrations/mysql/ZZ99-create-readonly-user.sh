#!/bin/sh
set -eu

readonly_user="${MYSQL_READONLY_USER:-query_readonly}"
readonly_password="${MYSQL_READONLY_PASSWORD:?MYSQL_READONLY_PASSWORD is required}"
readonly_tables="${MYSQL_READONLY_TABLES:-sales,customers}"

case "$readonly_user" in
  *[!A-Za-z0-9_]*|'') echo "MYSQL_READONLY_USER must be a simple identifier" >&2; exit 2 ;;
esac
case "$MYSQL_DATABASE" in
  *[!A-Za-z0-9_]*|'') echo "MYSQL_DATABASE must be a simple identifier" >&2; exit 2 ;;
esac
case "$readonly_password" in
  *"
"*|*""*) echo "MYSQL_READONLY_PASSWORD must be a single line" >&2; exit 2 ;;
esac

# Escape the password as a MySQL string literal without printing it. User,
# database and table identifiers are separately restricted above/below.
escaped_password=$(printf '%s' "$readonly_password" | sed "s/\\\\/\\\\\\\\/g; s/'/''/g")

grant_statements=""
old_ifs="$IFS"
IFS=','
for table in $readonly_tables; do
  case "$table" in
    *[!A-Za-z0-9_]*|'') echo "MYSQL_READONLY_TABLES contains an invalid table" >&2; exit 2 ;;
  esac
  table_exists=$(MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=socket -N -B -uroot -e \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${MYSQL_DATABASE}' AND table_name='${table}'")
  if [ "$table_exists" = "1" ]; then
    grant_statements="${grant_statements}GRANT SELECT ON \`${MYSQL_DATABASE}\`.\`${table}\` TO '${readonly_user}'@'%';
"
  fi
done
IFS="$old_ifs"

MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=socket -uroot <<EOSQL
CREATE USER IF NOT EXISTS '${readonly_user}'@'%' IDENTIFIED BY '${escaped_password}';
ALTER USER '${readonly_user}'@'%' IDENTIFIED BY '${escaped_password}';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${readonly_user}'@'%';
GRANT USAGE ON *.* TO '${readonly_user}'@'%';
${grant_statements}
FLUSH PRIVILEGES;
EOSQL
