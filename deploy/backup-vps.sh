#!/usr/bin/env bash
set -euo pipefail
umask 077

backup_dir=/var/backups/wb-ai-assistant
compose_file=/srv/wb-ai-assistant/docker-compose.yml
compose_env=/srv/wb-ai-assistant/.env
if [ "${WB_ASSISTANT_UPDATE_LOCK_HELD:-0}" != 1 ]; then
  exec 8>/var/lock/wb-assistant-update.lock
  flock -x 8
fi
mkdir -p "$backup_dir"
exec 9>"$backup_dir/.backup.lock"
flock -x 9
stamp=$(date -u +%Y%m%dT%H%M%SZ)
rotation=plan
case "${1:---prepare-only}" in
  --prepare-only) ;;
  --rotate) rotation=apply ;;
  *) printf 'Use --prepare-only or --rotate\n' >&2; exit 2 ;;
esac
retention=${2:-/usr/local/lib/wb-ai-assistant/backup_retention.py}
test -f "$retention"
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
# A database dump is already a separate component. Do not archive older dumps
# inside every new data archive: that caused several GB of redundant copies/day.
tar -C "$data_mount" --exclude='./backups' --exclude='./recovery-snapshots' -czf "$data_tmp" .
tar -tzf "$data_tmp" > /dev/null
tar -czf "$config_tmp" -C /etc/wb-ai-assistant fernet.key \
  -C /srv/wb-ai-assistant .env
tar -tzf "$config_tmp" > /dev/null

mv -- "$db_tmp" "$backup_dir/$stamp.postgres.dump"
mv -- "$data_tmp" "$backup_dir/$stamp.data.tar.gz"
mv -- "$config_tmp" "$backup_dir/$stamp.config.tar.gz"
python3 "$retention" seal --root "$backup_dir" --stamp "$stamp" > /dev/null
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
python3 "$retention" "$rotation" --root "$backup_dir" --stamp "$stamp" \
  --data-backups "$data_mount/backups"
printf 'Verified backup: %s\n' "$stamp"
