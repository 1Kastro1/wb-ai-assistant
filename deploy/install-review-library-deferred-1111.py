"""Install the retry scheduler without replacing the active audit checkpoint."""
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
BASE='30df0be204f03d3b7c976250c3ed7470024aea8b'

def run(*args,**kw):return subprocess.run(args,cwd=ROOT,check=True,text=True,**kw)
def output(*args):return run(*args,stdout=subprocess.PIPE).stdout.strip()

def main(expected):
    assert re.fullmatch('[0-9a-f]{40}',expected)
    os.umask(0o077)
    with open('/var/lock/wb-assistant-update.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        assert output('git','rev-parse','HEAD')==BASE
        assert not output('git','status','--porcelain','--untracked-files=no')
        previous=json.loads(Path('/root/review-library-complete-install-1111.json').read_text())
        stamp=Path('/var/backups/wb-ai-assistant/latest-verified').read_text().strip()
        assert stamp>previous['backup'],'New checkpoint backup is not ready'
        spec=importlib.util.spec_from_file_location('retention','/usr/local/lib/wb-ai-assistant/backup_retention.py')
        retention=importlib.util.module_from_spec(spec);spec.loader.exec_module(retention)
        retention.verify('/var/backups/wb-ai-assistant',stamp)
        before=json.loads(output('docker','compose','exec','-T','backend','python','-m','app.review_library_audit'))
        assert before['safe_mode'] is True and before['job']['status'] in ('queued','running')
        run('git','fetch','--no-tags','origin','main')
        assert output('git','rev-parse','origin/main')==expected
        allowed={'backend/app/review_library_audit.py','backend/tests/test_review_library_audit.py','SEQUENTIAL_REVIEW_LIBRARY.md'}
        assert set(output('git','diff','--name-only',BASE,expected).splitlines())<=allowed
        run('git','merge','--ff-only',expected)
        recipe=Path('/root/library-deferred-runtime-1111.Dockerfile')
        recipe.write_text('FROM wb-ai-assistant-backend:library-checked\nCOPY app/review_library_audit.py /app/app/review_library_audit.py\nUSER root\nRUN chmod a+r /app/app/review_library_audit.py\nUSER assistant\n')
        run('docker','build','--file',str(recipe),'--tag','wb-ai-assistant-backend:audit-deferred-checked','backend')
        run('docker','run','--rm','--entrypoint','python','-e','WB_SECRET_KEY_FILE=/tmp/library-import.key',
            'wb-ai-assistant-backend:audit-deferred-checked','-c',
            "from pathlib import Path; from cryptography.fernet import Fernet; Path('/tmp/library-import.key').write_bytes(Fernet.generate_key()); from app import review_library_audit; from app.main import app; print('UNPRIVILEGED_IMPORTS_OK')")
        run('docker','compose','stop','-t','180','worker')
        run('docker','tag','wb-ai-assistant-backend:audit-deferred-checked','wb-ai-assistant-backend:latest')
        run('docker','compose','up','-d','--no-deps','backend')
        for attempt in range(60):
            try:
                health=json.load(urllib.request.urlopen('https://assistant.blackmindworks.com/api/health',timeout=5))
                assert health['status']=='ok' and health['database']=='ok'
                break
            except Exception:
                if attempt==59:raise
                time.sleep(2)
        # Wake the job-level wait; each review's own retry deadline is preserved.
        # The new scheduler may process other reviews in the same market first.
        wake='''from app.db import Session,Job
import sys
with Session() as db:
    job=db.get(Job,sys.argv[1])
    assert job and not job.cancel_requested
    job.result={**(job.result or {}),'retry_at':0}
    db.commit()
'''
        run('docker','compose','exec','-T','backend','python','-c',wake,before['job']['id'])
        run('docker','compose','up','-d','--no-deps','worker')
        for service in ('backend','worker'):
            actual=output('docker','compose','exec','-T',service,'sha256sum','/app/app/review_library_audit.py').split()[0]
            assert actual==hashlib.sha256((ROOT/'backend/app/review_library_audit.py').read_bytes()).hexdigest()
        after=json.loads(output('docker','compose','exec','-T','backend','python','-m','app.review_library_audit'))
        assert after['safe_mode'] is True and after['job']['id']==before['job']['id']
        assert after['job']['checked']>=before['job']['checked']
        mount=output('docker','volume','inspect','--format','{{ .Mountpoint }}','wb-ai-assistant_wb_data')
        rotation=retention.apply('/var/backups/wb-ai-assistant',stamp,str(Path(mount)/'backups'))
        Path('/root/review-library-deferred-install-1111.json').write_text(json.dumps({
            'installed':expected,'backup':stamp,'health':health,'campaign':after,'before_checked':before['job']['checked'],
            'rotation_removed_bytes':rotation['remove_bytes']},ensure_ascii=False,indent=2))
        print('INSTALLED',expected,'CAMPAIGN',after['job']['id'],'CHECKED',after['job']['checked'],flush=True)

if __name__=='__main__':main(sys.argv[1])
