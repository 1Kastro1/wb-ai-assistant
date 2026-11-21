#!/usr/bin/env bash
set -euo pipefail
umask 077

dump=$(realpath -- "${1:?Specify the PostgreSQL dump}")
test -f "$dump" && test -s "$dump"
# Only the isolated database sees the dump. It has no network or shared live volume.
docker run --rm -i --network none --entrypoint sh postgres:17-alpine -ec '
  dir=$(mktemp -d)
  chown postgres:postgres "$dir"
  su-exec postgres initdb -D "$dir" -A trust >/dev/null
  su-exec postgres pg_ctl -D "$dir" -o "-c listen_addresses= -k $dir" -l "$dir/server.log" -w start >/dev/null
  export PGHOST="$dir"
  su-exec postgres createdb restore_check
  su-exec postgres pg_restore --no-owner --no-privileges --exit-on-error -d restore_check
  su-exec postgres psql -X -v ON_ERROR_STOP=1 -d restore_check -Atc "
    SELECT version_num FROM alembic_version;
    SELECT count(*) FROM reviews;
    SELECT count(*) FROM publication_jobs;
  "
  su-exec postgres pg_ctl -D "$dir" -m fast -w stop >/dev/null
' < "$dump"

data_mount=$(docker volume inspect -f '{{ .Mountpoint }}' wb-ai-assistant_wb_data)
python3 - "$data_mount/backups/restore-status.json" "$(basename "$dump")" <<'PY'
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

path, name = sys.argv[1:]
with tempfile.NamedTemporaryFile(mode='w', dir=os.path.dirname(path), delete=False) as stream:
    json.dump({'name': name, 'tested_at': datetime.now(timezone.utc).isoformat()}, stream)
os.chmod(stream.name, 0o644)
os.replace(stream.name, path)
PY
