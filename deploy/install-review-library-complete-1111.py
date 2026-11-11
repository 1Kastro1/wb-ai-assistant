"""Complete the assistant update with explicit application-file readability checks."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
ROOT=Path('/srv/wb-ai-assistant')
BASE='fbf2b3e482930d123a5007b9b305a92428e80e34'

def run(*args,**kw):return subprocess.run(args,cwd=ROOT,check=True,text=True,**kw)
def output(*args):return run(*args,stdout=subprocess.PIPE).stdout.strip()

def main(expected):
    assert re.fullmatch('[0-9a-f]{40}',expected)
    os.umask(0o077)
    with open('/var/lock/wb-assistant-update.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        assert output('git','rev-parse','HEAD')==BASE
        assert not output('git','status','--porcelain','--untracked-files=no')
        assert not output('docker','compose','ps','--status','running','-q','worker')
        stamp=Path('/var/backups/wb-ai-assistant/latest-verified').read_text().strip()
        assert stamp>'20261111T114732Z','New pre-activation backup is not ready'
        spec=importlib.util.spec_from_file_location('retention','/usr/local/lib/wb-ai-assistant/backup_retention.py')
        retention=importlib.util.module_from_spec(spec);spec.loader.exec_module(retention)
        retention.verify('/var/backups/wb-ai-assistant',stamp)
        run('git','fetch','--no-tags','origin','main')
        assert output('git','rev-parse','origin/main')==expected
        allowed={'backend/app/review_library_audit.py','backend/tests/test_review_library_audit.py','SEQUENTIAL_REVIEW_LIBRARY.md'}
        assert set(output('git','diff','--name-only',BASE,expected).splitlines())<=allowed
        run('git','merge','--ff-only',expected)
        recipe=Path('/root/library-complete-runtime-1111.Dockerfile')
        # Match the installed application's original Dockerfile permissions.
        # Keep source root-owned; grant the unprivileged runtime read access.
        recipe.write_text('FROM wb-ai-assistant-backend:before-library-1111\nCOPY app /app/app\nCOPY migrations /app/migrations\nUSER root\nRUN chmod -R a+rX /app/app /app/migrations\nUSER assistant\n')
        run('docker','build','--file',str(recipe),'--tag','wb-ai-assistant-backend:library-checked','backend')
        # Exercise imports/migration discovery as the actual unprivileged user
        # before replacing the healthy API. No production data is mounted.
        run('docker','run','--rm','--entrypoint','python','-e','WB_SECRET_KEY_FILE=/tmp/library-import.key','wb-ai-assistant-backend:library-checked','-c',
            "from pathlib import Path; from cryptography.fernet import Fernet; Path('/tmp/library-import.key').write_bytes(Fernet.generate_key()); from app import review_library_audit,verified_reply_library; from app.main import app; assert Path('/app/migrations/versions/0018_verified_reply_library.py').read_text(); print('UNPRIVILEGED_IMPORTS_OK')")
        run('docker','tag','wb-ai-assistant-backend:library-checked','wb-ai-assistant-backend:latest')
        run('docker','compose','up','-d','--no-deps','backend')
        for attempt in range(60):
            try:
                health=json.load(urllib.request.urlopen('https://assistant.blackmindworks.com/api/health',timeout=5))
                assert health['status']=='ok' and health['database']=='ok'
                assert urllib.request.urlopen('https://assistant.blackmindworks.com/',timeout=5).status==200
                break
            except Exception:
                if attempt==59:raise
                time.sleep(2)
        run('docker','compose','exec','-T','backend','python','-m','app.review_library_audit','--start')
        run('docker','compose','up','-d','--no-deps','worker')
        for service in ('backend','worker'):
            for relative in ('app/db.py','app/main.py','app/learned_reply_templates.py','app/verified_reply_library.py',
                    'app/review_library_audit.py','app/services.py','app/queue_policy.py','app/queue_overview.py','app/maintenance.py',
                    'migrations/versions/0018_verified_reply_library.py'):
                actual=output('docker','compose','exec','-T',service,'sha256sum','/app/'+relative).split()[0]
                assert actual==hashlib.sha256((ROOT/'backend'/relative).read_bytes()).hexdigest(),service+': '+relative
        campaign=json.loads(output('docker','compose','exec','-T','backend','python','-m','app.review_library_audit'))
        assert campaign['safe_mode'] is True and campaign['job']['status'] in ('queued','running')
        mount=output('docker','volume','inspect','--format','{{ .Mountpoint }}','wb-ai-assistant_wb_data')
        rotation=retention.apply('/var/backups/wb-ai-assistant',stamp,str(Path(mount)/'backups'))
        Path('/root/review-library-complete-install-1111.json').write_text(json.dumps({
            'installed':expected,'backup':stamp,'health':health,'campaign':campaign,
            'rotation_removed_bytes':rotation['remove_bytes']},ensure_ascii=False,indent=2))
        print('INSTALLED',expected,'CAMPAIGN',campaign['job']['id'],'CHECKED',campaign['job']['checked'],flush=True)

if __name__=='__main__':main(sys.argv[1])
