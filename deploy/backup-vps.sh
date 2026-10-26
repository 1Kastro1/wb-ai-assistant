#!/usr/bin/env bash
set -euo pipefail
umask 077

backup_dir=/var/backups/wb-ai-assistant
compose_file=/srv/wb-ai-assistant/docker-compose.yml
compose_env=/srv/wb-ai-assistant/.env
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$backup_dir"
db_tmp="$backup_dir/$stamp.postgres.dump.tmp"
data_tmp="$backup_dir/$stamp.data.tar.gz.tmp"
config_tmp="$backup_dir/$stamp.config.tar.gz.tmp"
trap 'rm -f -- "$db_tmp" "$data_tmp" "$config_tmp"' EXIT

docker compose -f "$compose_file" --env-file "$compose_env" exec -T postgres \
  pg_dump -U wb_assistant -d wb_assistant -Fc > "$db_tmp"
docker compose -f "$compose_file" --env-file "$compose_env" exec -T postgres \
  pg_restore --list < "$db_tmp" > /dev/null

data_mount=$(docker volume inspect -f '{{ .Mountpoint }}' wb-ai-assistant_wb_data)
test -d "$data_mount"
tar -C "$data_mount" -czf "$data_tmp" .
tar -tzf "$data_tmp" > /dev/null
tar -czf "$config_tmp" -C /etc/wb-ai-assistant fernet.key \
  -C /srv/wb-ai-assistant .env
tar -tzf "$config_tmp" > /dev/null

mv -- "$db_tmp" "$backup_dir/$stamp.postgres.dump"
mv -- "$data_tmp" "$backup_dir/$stamp.data.tar.gz"
mv -- "$config_tmp" "$backup_dir/$stamp.config.tar.gz"
# Non-sensitive metadata lets the application show the actual server backup.
python3 - "$data_mount/backups/server-status.json" "$stamp" "$(stat -c %s "$backup_dir/$stamp.postgres.dump")" <<'PY'
import json, os, sys
from datetime import datetime, timezone
path, stamp, size = sys.argv[1:]
with open(path + '.tmp', 'w') as stream:
    json.dump({'created_at': datetime.now(timezone.utc).isoformat(), 'name': stamp + '.postgres.dump',
               'size_mb': round(int(size)/1024/1024, 1), 'catalog_verified': True,
               'restore_tested': False}, stream)
os.chmod(path + '.tmp', 0o644)
os.replace(path + '.tmp', path)
PY
find "$backup_dir" -maxdepth 1 -type f -name '*.postgres.dump' -mtime +14 -delete
find "$backup_dir" -maxdepth 1 -type f -name '*.data.tar.gz' -mtime +14 -delete
find "$backup_dir" -maxdepth 1 -type f -name '*.config.tar.gz' -mtime +14 -delete
printf 'Verified backup: %s\n' "$stamp"
