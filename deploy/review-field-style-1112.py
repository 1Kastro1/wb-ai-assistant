"""Build a CSS-only frontend candidate from installed dependencies, then deploy."""
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
BASE='739e36b115fd4d329649373269a4c6bc76fd88d4'
SOURCE='cbee3f11989d48505b1a4aada7c3952878c78c58'
CSS_SHA='03e97c73beffb4e881cb1c0edb55d7eaeefd367a14537635fea7a5cf6d34583d'
IMAGE='wb-ai-assistant-frontend:review-field-style-checked-1112'
REPORT=Path('/root/review-style-build-1112.json')


def run(*args,**kw):return subprocess.run(args,cwd=ROOT,check=True,text=True,**kw)
def output(*args):return run(*args,stdout=subprocess.PIPE).stdout.strip()
def frontend():return output('docker','inspect','--format','{{ .Image }}','wb-ai-assistant-frontend-1')


def build():
    assert output('git','rev-parse','HEAD')==BASE
    original=frontend()
    assert re.fullmatch('sha256:[0-9a-f]{64}',original)
    run('docker','tag',original,'wb-ai-assistant-frontend:style-base-1112')
    folder=Path('/root/review-field-style-build-1112');folder.mkdir(exist_ok=True)
    raw=urllib.request.urlopen('https://raw.githubusercontent.com/1Kastro1/wb-ai-assistant/'+SOURCE+'/frontend/app/workbench.css',timeout=30).read()
    assert hashlib.sha256(raw).hexdigest()==CSS_SHA
    assert b'.customer-review-section .empty{' in raw and b'padding:0;background:transparent;border:0;text-align:left' in raw
    (folder/'workbench.css').write_bytes(raw)
    (folder/'Dockerfile').write_text('FROM wb-ai-assistant-frontend:style-base-1112\nUSER root\n'
        'COPY workbench.css /app/app/workbench.css\n'
        'RUN node node_modules/next/dist/bin/next build\n'
        'RUN chmod -R a+rX /app/.next && chmod a+r /app/app/workbench.css\nUSER node\n')
    run('docker','build','--tag',IMAGE,str(folder))
    run('docker','run','--rm','--entrypoint','node',IMAGE,'-e',
        "const fs=require('fs');fs.accessSync('/app/.next/BUILD_ID',fs.constants.R_OK);fs.accessSync('/app/app/workbench.css',fs.constants.R_OK);console.log('UNPRIVILEGED_FRONTEND_OK')")
    REPORT.write_text(json.dumps({'base_image':original,'source':SOURCE,'css_sha256':hashlib.sha256(raw).hexdigest(),'image':IMAGE}))
    print('STYLE_CANDIDATE_READY',flush=True)


def install(expected):
    assert re.fullmatch('[0-9a-f]{40}',expected)
    with open('/var/lock/wb-assistant-update.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        assert output('git','rev-parse','HEAD')==BASE
        assert not output('git','status','--porcelain','--untracked-files=no')
        prepared=json.loads(REPORT.read_text())
        assert frontend()==prepared['base_image']
        stamp=Path('/var/backups/wb-ai-assistant/latest-verified').read_text().strip()
        assert stamp>'20261112T081428Z'
        spec=importlib.util.spec_from_file_location('retention','/usr/local/lib/wb-ai-assistant/backup_retention.py')
        retention=importlib.util.module_from_spec(spec);spec.loader.exec_module(retention)
        retention.verify('/var/backups/wb-ai-assistant',stamp)
        run('git','fetch','--no-tags','origin','main')
        assert output('git','rev-parse','origin/main')==expected
        assert output('git','diff','--name-only',BASE,expected)=='frontend/app/workbench.css'
        run('git','merge','--ff-only',expected)
        assert hashlib.sha256((ROOT/'frontend/app/workbench.css').read_bytes()).hexdigest()==prepared['css_sha256']
        run('docker','tag',IMAGE,'wb-ai-assistant-frontend:latest')
        run('docker','compose','up','-d','--no-deps','frontend')
        for attempt in range(60):
            try:
                assert urllib.request.urlopen('https://assistant.blackmindworks.com/',timeout=10).status==200
                health=json.load(urllib.request.urlopen('https://assistant.blackmindworks.com/api/health',timeout=5))
                assert health['status']=='ok' and health['database']=='ok'
                break
            except Exception:
                if attempt==59:raise
                time.sleep(2)
        assert output('docker','compose','exec','-T','frontend','sha256sum','/app/app/workbench.css').split()[0]==prepared['css_sha256']
        audit=json.loads(output('docker','compose','exec','-T','backend','python','-m','app.review_library_audit'))
        assert audit['safe_mode'] is True
        check="from app.db import Session,setting; db=Session(); s=setting(db,'automation_schedule',{}); assert not any(s.get(k) for k in ('enabled','reviews_sync_enabled','questions_sync_enabled','drafts_enabled','question_drafts_enabled','publication_enabled')); db.close(); print('AUTOMATION_STILL_PAUSED')"
        run('docker','compose','exec','-T','backend','python','-c',check)
        mount=output('docker','volume','inspect','--format','{{ .Mountpoint }}','wb-ai-assistant_wb_data')
        rotation=retention.apply('/var/backups/wb-ai-assistant',stamp,str(Path(mount)/'backups'))
        Path('/root/review-field-style-install-1112.json').write_text(json.dumps({'installed':expected,'backup':stamp,
            'health':health,'audit':audit,'rotation_removed_bytes':rotation['remove_bytes']},ensure_ascii=False))
        print('REVIEW_FIELD_STYLE_INSTALLED',flush=True)


if __name__=='__main__':
    os.umask(0o077)
    if sys.argv[1]=='build':build()
    else:install(sys.argv[1])
