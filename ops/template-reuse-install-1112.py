"""Prebuild and install reviewed template reuse without enabling marketplace jobs."""
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
BASE='dcd2b87b70bbe2d0333978a773b1644933c1244a'
SOURCE='ec545f14bbe83be8e7dd723bbfc4dd0e33e284e9'
BACKEND='wb-ai-assistant-backend:template-reuse-checked-1112'
FRONTEND='wb-ai-assistant-frontend:template-reuse-checked-1112'
FOLDER=Path('/root/template-reuse-build-1112')
PREPARED=Path('/root/template-reuse-build-1112.json')
FILES=['backend/app/equivalent_reviews.py','backend/app/learned_reply_templates.py',
       'backend/app/review_library_audit.py','frontend/app/ReplyLibraryStatus.tsx']


def run(*args,**kw):return subprocess.run(args,cwd=ROOT,check=True,text=True,**kw)
def output(*args):return run(*args,stdout=subprocess.PIPE).stdout.strip()
def image(service):return output('docker','inspect','--format','{{ .Image }}','wb-ai-assistant-'+service+'-1')
def audit():return json.loads(output('docker','compose','exec','-T','backend','python','-m','app.review_library_audit'))


def build():
    assert output('git','rev-parse','HEAD')==BASE
    originals={s:image(s) for s in ('backend','frontend')}
    for service,value in originals.items():
        assert re.fullmatch('sha256:[0-9a-f]{64}',value)
        run('docker','tag',value,'wb-ai-assistant-'+service+':template-base-1112')
    FOLDER.mkdir(exist_ok=True)
    hashes={}
    for name in FILES:
        raw=urllib.request.urlopen('https://raw.githubusercontent.com/1Kastro1/wb-ai-assistant/'+SOURCE+'/'+name,timeout=30).read()
        target=FOLDER/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        hashes[name]=hashlib.sha256(raw).hexdigest()
    recipe=FOLDER/'backend.Dockerfile'
    recipe.write_text('FROM wb-ai-assistant-backend:template-base-1112\nUSER root\n'+
        ''.join('COPY '+name+' /app/app/'+Path(name).name+'\n' for name in FILES if name.startswith('backend/'))+
        'RUN chmod -R a+rX /app/app\nUSER assistant\n')
    run('docker','build','--file',str(recipe),'--tag',BACKEND,str(FOLDER))
    run('docker','run','--rm','--entrypoint','python','-e','WB_SECRET_KEY_FILE=/tmp/import.key',BACKEND,'-c',
        "from pathlib import Path; from cryptography.fernet import Fernet; Path('/tmp/import.key').write_bytes(Fernet.generate_key()); from app import equivalent_reviews,learned_reply_templates,review_library_audit; from app.main import app; assert equivalent_reviews.message_group('Нет запаха!')=='no_scent'; assert equivalent_reviews.message_group('Не пахнет, болит голова') is None; print('UNPRIVILEGED_TEMPLATE_IMPORTS_OK')")
    recipe=FOLDER/'frontend.Dockerfile'
    recipe.write_text('FROM wb-ai-assistant-frontend:template-base-1112\nUSER root\n'
        'COPY frontend/app/ReplyLibraryStatus.tsx /app/app/ReplyLibraryStatus.tsx\n'
        'RUN node node_modules/next/dist/bin/next build\n'
        'RUN chmod -R a+rX /app/.next && chmod a+r /app/app/ReplyLibraryStatus.tsx\nUSER node\n')
    run('docker','build','--file',str(recipe),'--tag',FRONTEND,str(FOLDER))
    run('docker','run','--rm','--entrypoint','node',FRONTEND,'-e',
        "const fs=require('fs');fs.accessSync('/app/.next/BUILD_ID',fs.constants.R_OK);console.log('UNPRIVILEGED_TEMPLATE_FRONTEND_OK')")
    PREPARED.write_text(json.dumps({'source':SOURCE,'base_images':originals,'hashes':hashes}))
    print('TEMPLATE_CANDIDATES_READY',flush=True)


def install(expected):
    assert re.fullmatch('[0-9a-f]{40}',expected)
    with open('/var/lock/wb-assistant-update.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        assert output('git','rev-parse','HEAD')==BASE
        assert not output('git','status','--porcelain','--untracked-files=no')
        prepared=json.loads(PREPARED.read_text())
        assert prepared['source']==SOURCE
        assert all(image(s)==v for s,v in prepared['base_images'].items())
        stamp=Path('/var/backups/wb-ai-assistant/latest-verified').read_text().strip()
        assert stamp.startswith('20261112')
        previous=json.loads(Path('/root/review-field-cascade-install-1112.json').read_text())
        assert stamp>previous['backup'],'Fresh full backup required before backend upgrade'
        spec=importlib.util.spec_from_file_location('retention','/usr/local/lib/wb-ai-assistant/backup_retention.py')
        retention=importlib.util.module_from_spec(spec);spec.loader.exec_module(retention)
        retention.verify('/var/backups/wb-ai-assistant',stamp)
        before=audit()
        assert before['safe_mode'] is True and before['job']['status'] in ('queued','running')
        run('git','fetch','--no-tags','origin','main')
        assert output('git','rev-parse','origin/main')==expected
        assert set(output('git','diff','--name-only',BASE,expected).splitlines())==set(FILES+['backend/tests/test_equivalent_review_templates.py'])
        run('git','merge','--ff-only',expected)
        for name,value in prepared['hashes'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value
        run('docker','compose','stop','-t','180','worker')
        run('docker','tag',BACKEND,'wb-ai-assistant-backend:latest')
        run('docker','tag',FRONTEND,'wb-ai-assistant-frontend:latest')
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
        indexed=json.loads(output('docker','compose','exec','-T','backend','python','-m','app.learned_reply_templates'))
        pending=output('docker','compose','exec','-T','backend','python','-m','app.review_library_audit','--reuse-templates')
        warmed=json.loads(pending.splitlines()[0])
        run('docker','compose','up','-d','--no-deps','worker')
        for service in ('backend','worker'):
            for name in FILES:
                if name.startswith('backend/'):
                    actual=output('docker','compose','exec','-T',service,'sha256sum','/app/app/'+Path(name).name).split()[0]
                    assert actual==prepared['hashes'][name]
        actual=output('docker','compose','exec','-T','frontend','sha256sum','/app/app/ReplyLibraryStatus.tsx').split()[0]
        assert actual==prepared['hashes']['frontend/app/ReplyLibraryStatus.tsx']
        after=audit()
        assert after['safe_mode'] is True and after['job']['id']==before['job']['id']
        assert after['job']['verified']>=before['job']['verified']
        check="from app.db import Session,setting; db=Session(); s=setting(db,'automation_schedule',{}); assert not any(s.get(k) for k in ('enabled','sync_enabled','reviews_sync_enabled','questions_sync_enabled','drafts_enabled','question_drafts_enabled','publication_enabled','auto_propose')); assert setting(db,'safe_mode',False) is True; db.close(); print('AUTOMATION_STILL_PAUSED')"
        run('docker','compose','exec','-T','backend','python','-c',check)
        mount=output('docker','volume','inspect','--format','{{ .Mountpoint }}','wb-ai-assistant_wb_data')
        rotation=retention.apply('/var/backups/wb-ai-assistant',stamp,str(Path(mount)/'backups'))
        Path('/root/template-reuse-install-1112.json').write_text(json.dumps({'installed':expected,'backup':stamp,
            'health':health,'indexed':indexed,'warm_pass':warmed,'before':before,'after':after,
            'rotation_removed_bytes':rotation['remove_bytes']},ensure_ascii=False))
        print('TEMPLATE_REUSE_INSTALLED',flush=True)
        print('indexed',indexed['indexed'],'pending_reused',warmed['reused'],'checked',after['job']['checked'],'template_reused',after['job'].get('template_reused',0),flush=True)


if __name__=='__main__':
    os.umask(0o077)
    if sys.argv[1]=='build':build()
    else:install(sys.argv[1])
