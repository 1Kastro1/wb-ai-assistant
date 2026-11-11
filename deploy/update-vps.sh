#!/usr/bin/env bash
# A failed build/health check exits before rotation; previous backups remain.
set -euo pipefail
umask 077
cd /srv/wb-ai-assistant
exec 8>/var/lock/wb-assistant-update.lock
flock -n 8
WB_ASSISTANT_UPDATE_LOCK_HELD=1 /usr/local/sbin/wb-assistant-backup --prepare-only
stamp=$(cat /var/backups/wb-ai-assistant/latest-verified)
git pull --ff-only origin main
docker compose up -d --build
python3 - <<'PY'
import json, time, urllib.request
for attempt in range(60):
    try:
        health=json.load(urllib.request.urlopen('https://assistant.blackmindworks.com/api/health',timeout=5))
        assert health['status']=='ok' and health['database']=='ok'
        assert urllib.request.urlopen('https://assistant.blackmindworks.com/',timeout=5).status==200
        break
    except Exception:
        if attempt==59: raise
        time.sleep(2)
PY
test -n "$(docker compose ps --status running -q backend)"
test -n "$(docker compose ps --status running -q frontend)"
test -n "$(docker compose ps --status running -q worker)"
data_mount=$(docker volume inspect -f '{{ .Mountpoint }}' wb-ai-assistant_wb_data)
python3 /usr/local/lib/wb-ai-assistant/backup_retention.py apply \
  --stamp "$stamp" --data-backups "$data_mount/backups"
