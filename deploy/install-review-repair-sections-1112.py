"""Install checked assistant code, preserve the running audit and paused sending."""
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
BASE='7c6944a539bcd933300c9870d0d936a10a56ec82'
BACKEND='wb-ai-assistant-backend:repair-sections-checked-1112'


def run(*args,**kw):return subprocess.run(args,cwd=ROOT,check=True,text=True,**kw)
def output(*args):return run(*args,stdout=subprocess.PIPE).stdout.strip()
def audit():return json.loads(output('docker','compose','exec','-T','backend','python','-m','app.review_library_audit'))


def main(expected):
    assert re.fullmatch('[0-9a-f]{40}',expected)
    os.umask(0o077)
    with open('/var/lock/wb-assistant-update.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        assert output('git','rev-parse','HEAD')==BASE
        assert not output('git','status','--porcelain','--untracked-files=no')
        stamp=Path('/var/backups/wb-ai-assistant/latest-verified').read_text().strip()
        assert stamp.startswith('20261112'), 'A fresh pre-install backup is required'
        spec=importlib.util.spec_from_file_location('retention','/usr/local/lib/wb-ai-assistant/backup_retention.py')
        retention=importlib.util.module_from_spec(spec);spec.loader.exec_module(retention)
        retention.verify('/var/backups/wb-ai-assistant',stamp)
        before=audit()
        assert before['safe_mode'] is True and before['job']['status'] in ('queued','running')
        run('git','fetch','--no-tags','origin','main')
        assert output('git','rev-parse','origin/main')==expected
        allowed={'backend/app/db.py','backend/app/integrations.py','backend/app/ozon_reviews.py',
            'backend/app/review_sections.py','backend/migrations/versions/0019_review_sections.py',
            'backend/app/maintenance.py','backend/tests/test_portable_backup.py','frontend/app/CustomerReview.tsx',
            'frontend/app/ReviewCard.tsx','frontend/app/workbench.css','frontend/tests/review-card.test.cjs',
            'backend/app/auto_review_repair.py','backend/app/review_library_audit.py',
            'backend/tests/test_automatic_review_repair.py','backend/tests/test_review_sections.py',
            'frontend/app/ReplyLibraryStatus.tsx','backend/app/attention_queue.py','backend/app/services.py'}
        assert set(output('git','diff','--name-only',BASE,expected).splitlines())<=allowed
        run('git','merge','--ff-only',expected)
        recipe=Path('/root/repair-sections-runtime-1112.Dockerfile')
        recipe.write_text('FROM wb-ai-assistant-backend:audit-deferred-checked\n'
            'COPY app /app/app\nCOPY migrations /app/migrations\n'
            'USER root\nRUN chmod -R a+rX /app/app /app/migrations\nUSER assistant\n')
        run('docker','build','--file',str(recipe),'--tag',BACKEND,'backend')
        run('docker','run','--rm','--entrypoint','python','-e','WB_SECRET_KEY_FILE=/tmp/import.key',BACKEND,'-c',
            "from pathlib import Path; from cryptography.fernet import Fernet; Path('/tmp/import.key').write_bytes(Fernet.generate_key()); from app import review_library_audit,auto_review_repair,review_sections; from app.main import app; print('UNPRIVILEGED_IMPORTS_OK')")
        # Builds run before service replacement. Previous containers keep serving.
        run('docker','compose','build','frontend')
        run('docker','compose','stop','-t','180','worker')
        run('docker','tag',BACKEND,'wb-ai-assistant-backend:latest')
        run('docker','compose','up','-d','--no-deps','backend','frontend')
        for attempt in range(90):
            try:
                health=json.load(urllib.request.urlopen('https://assistant.blackmindworks.com/api/health',timeout=5))
                assert health['status']=='ok' and health['database']=='ok'
                assert urllib.request.urlopen('https://assistant.blackmindworks.com/',timeout=10).status==200
                break
            except Exception:
                if attempt==89:raise
                time.sleep(2)
        upgrade='''from app.db import Session,Job
from app.review_library_audit import enable_automatic_repair
import sys
with Session() as db:
    job=db.get(Job,sys.argv[1])
    assert job and not job.cancel_requested
    enable_automatic_repair(db,job.id)
    db.commit()
'''
        run('docker','compose','exec','-T','backend','python','-c',upgrade,before['job']['id'])
        run('docker','compose','up','-d','--no-deps','worker')
        for service in ('backend','worker'):
            for name in ('review_library_audit.py','auto_review_repair.py','review_sections.py','db.py'):
                actual=output('docker','compose','exec','-T',service,'sha256sum','/app/app/'+name).split()[0]
                assert actual==hashlib.sha256((ROOT/'backend/app'/name).read_bytes()).hexdigest()
        after=audit()
        assert after['safe_mode'] is True and after['job']['id']==before['job']['id']
        assert after['job']['automated_repair'] is True
        assert after['job']['verified']>=before['job']['verified']
        verify='''from app.db import Session,Review,setting
from sqlalchemy import inspect
from app.db import engine
with Session() as db:
    assert 'sections' in {c['name'] for c in inspect(engine).get_columns('reviews')}
    assert setting(db,'safe_mode',False) is True
    schedule=setting(db,'automation_schedule',{})
    assert not any(schedule.get(k) for k in ('enabled','reviews_sync_enabled','questions_sync_enabled','drafts_enabled','question_drafts_enabled','publication_enabled'))
print('SCHEMA_PAUSE_AND_SAFETY_OK')
'''
        run('docker','compose','exec','-T','backend','python','-c',verify)
        mount=output('docker','volume','inspect','--format','{{ .Mountpoint }}','wb-ai-assistant_wb_data')
        rotation=retention.apply('/var/backups/wb-ai-assistant',stamp,str(Path(mount)/'backups'))
        Path('/root/review-repair-sections-install-1112.json').write_text(json.dumps({
            'installed':expected,'backup':stamp,'health':health,'campaign':after,
            'before_checked':before['job']['checked'],'before_verified':before['job']['verified'],
            'rotation_removed_bytes':rotation['remove_bytes']},ensure_ascii=False,indent=2))
        print('INSTALLED',expected,'AUDIT_REPAIR_ENABLED',flush=True)


if __name__=='__main__':main(sys.argv[1])
