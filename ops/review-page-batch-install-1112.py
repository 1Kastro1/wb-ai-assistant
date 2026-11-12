"""Deploy the checked catalog lookup fix without changing automation or audit state."""
import fcntl, hashlib, importlib.util, json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path
ROOT = Path('/srv/wb-ai-assistant')
BASE = 'a645e9b4224506ddfb979d5be03bc82f46a05644'
SOURCE = '17c002e660ac78e513736876f18df05640d875b3'
IMAGE = 'wb-ai-assistant-backend:review-page-batch-checked-1112'
FOLDER = Path('/root/review-page-batch-build-1112')
PREPARED = Path('/root/review-page-batch-build-1112.json')
FILES = ['backend/app/services.py','backend/app/shared_marketplaces.py','backend/app/owner_reply_rules.py']
FRONT_FILES = ['frontend/app/workspace-data.mjs']
FRONT_IMAGE = 'wb-ai-assistant-frontend:review-page-batch-checked-1112'
TESTS = ['backend/tests/test_catalog_lookup_performance.py','backend/tests/test_operations_postgres.py','frontend/tests/workbench.test.mjs']

def run(*args, **kw):
    return subprocess.run(args, cwd=ROOT, check=True, text=True, **kw)

def output(*args):
    return run(*args, stdout=subprocess.PIPE).stdout.strip()

def image(service='backend'):
    return output('docker', 'inspect', '--format', '{{ .Image }}', 'wb-ai-assistant-'+service+'-1')

def audit():
    return json.loads(output('docker', 'compose', 'exec', '-T', 'backend', 'python', '-m', 'app.review_library_audit'))

CHECK = '''import json
from sqlalchemy import text
from app.db import Session,setting
with Session() as db:
    assert db.scalar(text('SELECT version_num FROM alembic_version'))=='0020'
    rows=db.execute(text("SELECT c.relname,i.indisvalid,pg_relation_size(c.oid) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname IN ('ix_products_brand_article_lookup','ix_products_legacy_brand_lookup')")).all()
    assert len(rows)==2 and all(r[1] for r in rows),rows
    assert setting(db,'safe_mode',False) is True
    schedule=setting(db,'automation_schedule',{})
    assert not any(schedule.get(k) for k in ('enabled','reviews_sync_enabled','questions_sync_enabled','drafts_enabled','question_drafts_enabled','publication_enabled'))
    print(json.dumps({'indexes':[list(r) for r in rows],'schedule':schedule,'safe_mode':True}))
'''

def build():
    assert output('git', 'rev-parse', 'HEAD') == BASE
    base = image(); front_base = image('frontend')
    run('docker','tag',front_base,'wb-ai-assistant-frontend:review-page-batch-base-1112')
    run('docker', 'tag', base, 'wb-ai-assistant-backend:review-page-batch-base-1112')
    hashes = {}
    for name in FILES+FRONT_FILES:
        raw = urllib.request.urlopen('https://raw.githubusercontent.com/1Kastro1/wb-ai-assistant/' + SOURCE + '/' + name, timeout=30).read()
        target = (FOLDER / 'runtime' / Path(name).relative_to('backend')) if name in FILES else FOLDER/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    (FOLDER/'Dockerfile').write_text('FROM wb-ai-assistant-backend:review-page-batch-base-1112\nCOPY --chown=assistant:assistant runtime/ /app/\n')
    run('docker', 'build', '--tag', IMAGE, str(FOLDER))
    run('docker','run','--rm','--entrypoint','python','-e','WB_SECRET_KEY_FILE=/tmp/import.key',IMAGE,'-c',
        "from pathlib import Path;from cryptography.fernet import Fernet;Path('/tmp/import.key').write_bytes(Fernet.generate_key());from app import catalog_lookup_sql;from app.main import app;print('UNPRIVILEGED_CATALOG_IMPORTS_OK')")
    front_recipe=FOLDER/'frontend.Dockerfile'
    front_recipe.write_text('FROM wb-ai-assistant-frontend:review-page-batch-base-1112\nUSER root\nCOPY --chown=node:node frontend/app/workspace-data.mjs /app/app/workspace-data.mjs\nRUN node node_modules/next/dist/bin/next build\nUSER node\n')
    run('docker','build','--file',str(front_recipe),'--tag',FRONT_IMAGE,str(FOLDER))
    run('docker','run','--rm','--entrypoint','node',FRONT_IMAGE,'-e',"require('fs').accessSync('/app/.next/BUILD_ID',require('fs').constants.R_OK);console.log('BATCH_FRONTEND_READABLE')")
    PREPARED.write_text(json.dumps({'source':SOURCE, 'base_image':base,'front_base':front_base, 'hashes':hashes}))
    print('BATCH_PAGE_CANDIDATE_READY', flush=True)

def install(expected):
    assert re.fullmatch('[0-9a-f]{40}', expected)
    with open('/var/lock/wb-assistant-update.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert output('git','rev-parse','HEAD') == BASE
        assert not output('git','status','--porcelain','--untracked-files=no')
        prepared = json.loads(PREPARED.read_text())
        assert image() == prepared['base_image'] and image('frontend')==prepared['front_base'] and prepared['source'] == SOURCE
        stamp = Path('/var/backups/wb-ai-assistant/latest-verified').read_text().strip()
        assert stamp > json.loads(Path('/root/catalog-fast-install-1112.json').read_text())['backup']
        spec = importlib.util.spec_from_file_location('retention','/usr/local/lib/wb-ai-assistant/backup_retention.py')
        retention = importlib.util.module_from_spec(spec); spec.loader.exec_module(retention)
        retention.verify('/var/backups/wb-ai-assistant', stamp)
        before = audit()
        assert before['safe_mode'] is True and before['job']['status'] in ('queued','running')
        run('git','fetch','--no-tags','origin','main')
        assert output('git','rev-parse','origin/main') == expected
        assert set(output('git','diff','--name-only',BASE,expected).splitlines()) == set(FILES+FRONT_FILES+TESTS)
        run('git','merge','--ff-only',expected)
        for name, sha in prepared['hashes'].items():
            assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == sha
        run('docker','compose','stop','-t','180','worker')
        run('docker','tag',IMAGE,'wb-ai-assistant-backend:latest')
        run('docker','tag',FRONT_IMAGE,'wb-ai-assistant-frontend:latest')
        run('docker','compose','up','-d','--no-deps','backend','frontend')
        for attempt in range(90):
            try:
                health = json.load(urllib.request.urlopen('https://assistant.blackmindworks.com/api/health',timeout=5))
                assert health['status']=='ok' and health['database']=='ok'
                break
            except Exception:
                if attempt==89: raise
                time.sleep(2)
        checks = json.loads(output('docker','compose','exec','-T','backend','python','-c',CHECK))
        run('docker','compose','up','-d','--no-deps','worker')
        for service in ('backend','worker'):
            for name in FILES:
                sha=prepared['hashes'][name]
                path='/app/'+str(Path(name).relative_to('backend'))
                assert output('docker','compose','exec','-T',service,'sha256sum',path).split()[0]==sha
        for name in FRONT_FILES:
            assert output('docker','compose','exec','-T','frontend','sha256sum','/app/'+str(Path(name).relative_to('frontend'))).split()[0]==prepared['hashes'][name]
        after = audit()
        assert after['job']['id'] == before['job']['id'] and after['safe_mode'] is True
        assert urllib.request.urlopen('https://assistant.blackmindworks.com/',timeout=10).status==200
        mount=output('docker','volume','inspect','--format','{{ .Mountpoint }}','wb-ai-assistant_wb_data')
        rotation=retention.apply('/var/backups/wb-ai-assistant',stamp,str(Path(mount)/'backups'))
        Path('/root/review-page-batch-install-1112.json').write_text(json.dumps({'installed':expected,'backup':stamp,
            'before':before,'after':after,'checks':checks,'health':health,'rotation_removed_bytes':rotation['remove_bytes']},ensure_ascii=False))
        print('BATCH_PAGE_INSTALLED',flush=True)

if __name__ == '__main__':
    os.umask(0o077)
    if sys.argv[1]=='build': build()
    else: install(sys.argv[1])
