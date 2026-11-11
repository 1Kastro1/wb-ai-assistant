import datetime, hashlib, json, pathlib, shutil, subprocess, sys, time, urllib.request

ROOT=pathlib.Path('/srv/wb-ai-assistant')
STATE=pathlib.Path('/root/review-verification-1111.json')
EXPECTED='f1623eab716371f00deab823de21ba214a57259b'

def run(args,**kw):return subprocess.run(args,cwd=ROOT,check=True,**kw)
def out(args):return subprocess.check_output(args,cwd=ROOT,text=True).strip()
def save(state):STATE.write_text(json.dumps(state,indent=2))

def prepare():
    if STATE.exists():raise RuntimeError('Preparation already exists; inspect before resuming')
    dirty=out(['git','diff','--name-only']).splitlines()
    assert not set(dirty)-{'deploy/backup-vps.sh'},dirty
    run(['git','fetch','origin','refs/pull/156/head:refs/remotes/origin/pr156'])
    assert out(['git','rev-parse','origin/pr156'])==EXPECTED
    old=out(['git','rev-parse','HEAD'])
    run(['git','merge-base','--is-ancestor',old,EXPECTED])
    # Runtime-only image reuse is permitted only if dependencies are unchanged.
    run(['git','diff','--exit-code',old,EXPECTED,'--','backend/requirements.lock.txt','backend/Dockerfile','docker-compose.yml','frontend'])
    assert shutil.disk_usage(ROOT).free>2*1024**3
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ').lower()
    folder=pathlib.Path('/var/backups/wb-ai-assistant')/('before-pr156-'+stamp)
    folder.mkdir(mode=0o700)
    (folder/'tracked.patch').write_text(out(['git','diff']))
    state={'old':old,'target':EXPECTED,'stamp':stamp,'backup':str(folder)}
    for svc in ('backend','worker'):
        cid=out(['docker','compose','ps','-q',svc])
        image=out(['docker','inspect','--format','{{.Image}}',cid])
        tag='wb-ai-assistant-'+svc+':before-pr156-'+stamp
        run(['docker','tag',image,tag]);state[svc]={'image':image,'tag':tag}
    save(state)
    dump=folder/'postgres.dump'
    with dump.open('wb') as f:
        run(['docker','compose','exec','-T','postgres','pg_dump','-U','wb_assistant','-d','wb_assistant','-Fc'],stdout=f)
    assert dump.stat().st_size>1000000
    with dump.open('rb') as f:
        run(['docker','compose','exec','-T','postgres','pg_restore','--list'],stdin=f,stdout=subprocess.DEVNULL)
    test_db='verify_pr156_'+stamp
    run(['docker','compose','exec','-T','postgres','createdb','-U','wb_assistant',test_db])
    with dump.open('rb') as f:
        run(['docker','compose','exec','-T','postgres','pg_restore','-U','wb_assistant','--exit-on-error','--no-owner','-d',test_db],stdin=f)
    assert test_db.startswith('verify_pr156_') and test_db!='wb_assistant'
    run(['docker','compose','exec','-T','postgres','dropdb','-U','wb_assistant',test_db])
    state.update(backup_verified=True,backup_bytes=dump.stat().st_size)
    save(state);print('PREPARED',json.dumps(state),flush=True)

def install():
    state=json.loads(STATE.read_text());assert state['backup_verified']
    assert out(['git','rev-parse','HEAD'])==state['old']
    run(['git','fetch','origin','main'])
    target=out(['git','rev-parse','origin/main'])
    # The approved merge must contain the exact CI-tested tree.
    assert out(['git','rev-parse',target+'^{tree}'])==out(['git','rev-parse',EXPECTED+'^{tree}'])
    run(['git','switch','-c','deploy/pr156-'+state['stamp'],target])
    state['installed_commit']=target;save(state)
    dockerfile=pathlib.Path('/root/pr156-runtime.Dockerfile')
    dockerfile.write_text('FROM '+state['backend']['tag']+'\nCOPY --chown=10001:10001 app /app/app\n')
    run(['docker','build','-f',str(dockerfile),'-t','wb-ai-assistant-backend','backend'])
    run(['docker','compose','stop','-t','180','worker'])
    try:
        run(['docker','compose','up','-d','--no-deps','--force-recreate','backend'])
        for _ in range(60):
            cid=out(['docker','compose','ps','-q','backend'])
            if out(['docker','inspect','--format','{{.State.Health.Status}}',cid])=='healthy':break
            time.sleep(2)
        else:raise RuntimeError('Backend did not become healthy')
        run(['docker','compose','up','-d','--no-deps','--force-recreate','worker'])
        # Check the actual files in both active processes against Git blobs.
        for svc in ('backend','worker'):
            for path in ('draft_audit.py','generation_control.py','review_regeneration.py','rule_execution.py'):
                expected=hashlib.sha256(subprocess.check_output(['git','show',target+':backend/app/'+path],cwd=ROOT)).hexdigest()
                actual=out(['docker','compose','exec','-T',svc,'sha256sum','/app/app/'+path]).split()[0]
                assert actual==expected,(svc,path,actual,expected)
        health=json.load(urllib.request.urlopen('https://assistant.blackmindworks.com/api/health',timeout=20))
        assert health['status']=='ok' and health['database']=='ok',health
    except Exception:
        run(['docker','tag',state['backend']['image'],'wb-ai-assistant-backend'])
        run(['git','switch','-c','rollback/pr156-'+state['stamp'],state['old']])
        run(['docker','compose','up','-d','--no-deps','--force-recreate','backend','worker'])
        raise
    state.update(installed=True,health=health);save(state)
    print('INSTALLED',json.dumps(state),flush=True)
    run(['docker','compose','ps'])

if __name__=='__main__':
    {'prepare':prepare,'install':install}[sys.argv[1]]()

