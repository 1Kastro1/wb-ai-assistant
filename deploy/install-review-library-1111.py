"""Controlled update of the existing assistant, using its installed dependencies."""
import fcntl
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
BASE='4d92350593fd8dbe10eb0472f35db0ea197e3419'

def run(*args,**kw):
    return subprocess.run(args,cwd=ROOT,check=True,text=True,**kw)

def output(*args):return run(*args,stdout=subprocess.PIPE).stdout.strip()


def main(expected):
    assert re.fullmatch('[0-9a-f]{40}',expected)
    os.umask(0o077)
    with open('/var/lock/wb-assistant-update.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        assert output('git','rev-parse','HEAD')==BASE,'Unexpected production revision'
        assert not output('git','status','--porcelain','--untracked-files=no'),'Production checkout has local changes'
        assert not output('docker','compose','ps','--status','running','-q','worker'),'Worker must remain stopped'
        stamp=Path('/var/backups/wb-ai-assistant/latest-verified').read_text().strip()
        assert stamp>'20261111T103132Z','New pre-update backup is not ready'
        spec=importlib.util.spec_from_file_location('retention','/usr/local/lib/wb-ai-assistant/backup_retention.py')
        retention=importlib.util.module_from_spec(spec);spec.loader.exec_module(retention)
        retention.verify('/var/backups/wb-ai-assistant',stamp)
        run('git','fetch','--no-tags','origin','main')
        assert output('git','rev-parse','origin/main')==expected,'Main changed; inspect before installing'
        assert not output('git','diff',BASE,expected,'--','backend/requirements.lock.txt','backend/Dockerfile'), 'Runtime dependencies changed'
        run('git','merge','--ff-only',expected)
        run('docker','tag','wb-ai-assistant-backend:latest','wb-ai-assistant-backend:before-library-1111')
        recipe=Path('/root/library-runtime-1111.Dockerfile')
        recipe.write_text('FROM wb-ai-assistant-backend:before-library-1111\nCOPY app /app/app\nCOPY migrations /app/migrations\n')
        run('docker','build','--file',str(recipe),'--tag','wb-ai-assistant-backend:latest','backend')
        run('docker','compose','build','frontend')
        run('docker','compose','up','-d','--no-deps','backend','frontend')
        for attempt in range(60):
            try:
                health=json.load(urllib.request.urlopen('https://assistant.blackmindworks.com/api/health',timeout=5))
                assert health['status']=='ok' and health['database']=='ok'
                assert urllib.request.urlopen('https://assistant.blackmindworks.com/',timeout=5).status==200
                break
            except Exception:
                if attempt==59:raise
                time.sleep(2)
        # No imports or publications can run before this transaction commits.
        run('docker','compose','exec','-T','backend','python','-m','app.review_library_audit','--start')
        run('docker','compose','up','-d','--no-deps','worker')
        assert output('docker','compose','ps','--status','running','-q','worker')
        # Verify both processes received every changed application file.
        for service in ('backend','worker'):
            for relative in ('app/db.py','app/main.py','app/learned_reply_templates.py','app/verified_reply_library.py',
                    'app/review_library_audit.py','app/services.py','app/queue_policy.py','app/queue_overview.py','app/maintenance.py',
                    'migrations/versions/0018_verified_reply_library.py'):
                import hashlib
                host=hashlib.sha256((ROOT/'backend'/relative).read_bytes()).hexdigest()
                installed=output('docker','compose','exec','-T',service,'sha256sum','/app/'+relative).split()[0]
                assert host==installed,service+': '+relative
        mount=output('docker','volume','inspect','--format','{{ .Mountpoint }}','wb-ai-assistant_wb_data')
        rotation=retention.apply('/var/backups/wb-ai-assistant',stamp,str(Path(mount)/'backups'))
        result={'installed':expected,'backup':stamp,'health':health,'rotation_removed_bytes':rotation['remove_bytes'],
            'campaign':json.loads(output('docker','compose','exec','-T','backend','python','-m','app.review_library_audit'))}
        Path('/root/review-library-install-1111.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print('INSTALLED',expected,'BACKUP',stamp,flush=True)


if __name__=='__main__':main(sys.argv[1])
