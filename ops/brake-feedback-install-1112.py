"""Install reviewed brake feedback and resolve only the two diagnosed cases."""
import fcntl,hashlib,importlib.util,json,os,re,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path('/srv/wb-ai-assistant')
BASE='7f1ca6d59f2a0cf894ed93565470c9877bd607d5'
SOURCE='289653df2f6f3e542802795d2f140c44510a58e5'
IMAGE='wb-ai-assistant-backend:brake-feedback-checked-1112'
FOLDER=Path('/root/brake-feedback-build-1112')
PREPARED=Path('/root/brake-feedback-build-1112.json')
FILES=['backend/app/brake_feedback.py','backend/app/reply_policy.py','backend/app/owner_reply_policy.py',
    'backend/app/auto_review_repair.py','backend/app/review_cases.py','backend/app/owner_rule_profiles.json']
def run(*args,**kw):return subprocess.run(args,cwd=ROOT,check=True,text=True,**kw)
def output(*args):return run(*args,stdout=subprocess.PIPE).stdout.strip()
def audit():return json.loads(output('docker','compose','exec','-T','backend','python','-m','app.review_library_audit'))
def image():return output('docker','inspect','--format','{{ .Image }}','wb-ai-assistant-backend-1')
def targets():
    rows=json.loads(Path('/root/attention.json').read_text())['cases']
    assert len(rows)==2
    ids=[c['review']['id'] for c in rows]
    assert len(set(ids))==2 and all(re.fullmatch('[A-Za-z0-9:_-]+',v) for v in ids)
    return ids

PREFLIGHT='''import asyncio,sys
from app.db import Session,Review,Product,Draft
from app.brake_feedback import case,template,OLD_RULE,RULE
from app.owner_reply_rules import matching_owner_rules
from app.owner_reply_policy import enforce_owner_rules
from app.services import assess_reply,review_verified_facts
from app.review_library_audit import publication_protected
from app.draft_audit import manually_edited
from sqlalchemy import select
keys=[]
with Session() as db:
    for rid in sys.argv[1:]:
        r=db.get(Review,rid);p=db.get(Product,r.product_id)
        assert not publication_protected(db,r)
        d=db.scalar(select(Draft).where(Draft.review_id==rid))
        assert r.is_answered or not d or not manually_edited(d)
        rules=[RULE if rule==OLD_RULE else rule for rule in matching_owner_rules(db,p)]
        value=case(r,p);answer=template(r,p,rules)
        assert value and answer
        facts=review_verified_facts(db,r,p,rules)
        quality=assess_reply(r,p,answer,facts)
        assert quality['passed'],quality
        checked,proof=asyncio.run(enforce_owner_rules(answer,r,p,rules,facts,lambda v:assess_reply(r,p,v,facts),local_only=True))
        assert checked==answer and proof['passed'] and proof['verification_method']=='local_brake_feedback'
        keys.append(value['key'])
assert sorted(keys)==['brake_positive_mileage','brake_untried_packaging']
print('REAL_CASES_PREFLIGHT_OK',','.join(keys))
'''

ACTIVATE='''import asyncio,json,sys
from sqlalchemy import select
from app.db import Session,Job,Review,ReviewLibraryAuditRow,Draft,setting
from app.brake_feedback import upgrade_policy
from app.review_library_audit import inspect_review,reuse_pending_templates,KIND,report
from app.owner_reply_policy import fingerprint
from app.owner_reply_rules import matching_owner_rules
from app.db import Product
ids=sys.argv[1:]
with Session() as db:
    job=db.scalar(select(Job).where(Job.kind==KIND).order_by(Job.created_at.desc()).limit(1));jid=job.id
    assert all(db.get(ReviewLibraryAuditRow,(jid,rid)).status=='attention' for rid in ids)
    before={rid:db.get(Review,rid).existing_answer for rid in ids}
    upgraded=upgrade_policy(db,jid);db.commit()
outcomes={rid:asyncio.run(inspect_review(jid,rid,Session)) for rid in ids}
assert sorted(v['status'] for v in outcomes.values())==['verified','verified_reference'],outcomes
warm=asyncio.run(reuse_pending_templates(jid))
with Session() as db:
    for rid in ids:
        row=db.get(ReviewLibraryAuditRow,(jid,rid))
        assert row.status in ('verified','verified_reference') and row.template_id
        r=db.get(Review,rid)
        assert r.existing_answer==before[rid]
        if not r.is_answered:
            d=db.scalar(select(Draft).where(Draft.review_id==rid))
            p=db.get(Product,r.product_id);rules=matching_owner_rules(db,p)
            assert d.quality['passed'] and d.quality['owner_rules']['passed'] and d.quality['owner_rules']['checked']
            assert d.quality['owner_rules']['fingerprint']==fingerprint(rules)
            assert not d.quality['owner_rules'].get('issues') and not d.quality['owner_rules'].get('technical_error')
    assert setting(db,'safe_mode',False) is True
    schedule=setting(db,'automation_schedule',{})
    assert not any(schedule.get(k) for k in ('enabled','reviews_sync_enabled','questions_sync_enabled','drafts_enabled','question_drafts_enabled','publication_enabled'))
    print(json.dumps({'upgraded':upgraded,'outcomes':outcomes,'warm':warm,'audit':report(db)},ensure_ascii=False))
'''

def build():
    assert output('git','rev-parse','HEAD')==BASE
    base=image();run('docker','tag',base,'wb-ai-assistant-backend:brake-base-1112')
    FOLDER.mkdir(exist_ok=True);(FOLDER/'app').mkdir(exist_ok=True)
    hashes={}
    for name in FILES:
        raw=urllib.request.urlopen('https://raw.githubusercontent.com/1Kastro1/wb-ai-assistant/'+SOURCE+'/'+name,timeout=30).read()
        (FOLDER/'app'/Path(name).name).write_bytes(raw);hashes[name]=hashlib.sha256(raw).hexdigest()
    (FOLDER/'Dockerfile').write_text('FROM wb-ai-assistant-backend:brake-base-1112\nCOPY --chown=assistant:assistant app/ /app/app/\n')
    run('docker','build','--tag',IMAGE,str(FOLDER))
    run('docker','run','--rm','--entrypoint','python','-e','WB_SECRET_KEY_FILE=/tmp/import.key',IMAGE,'-c',
        "from pathlib import Path;from cryptography.fernet import Fernet;Path('/tmp/import.key').write_bytes(Fernet.generate_key());from app import brake_feedback;from app.main import app;print('UNPRIVILEGED_BRAKE_IMPORTS_OK')")
    override=FOLDER/'candidate.yml';override.write_text('services:\n  backend:\n    image: '+IMAGE+'\n')
    run('docker','compose','-f','docker-compose.yml','-f',str(override),'run','--rm','--no-deps','--entrypoint','python','backend','-c',PREFLIGHT,*targets())
    PREPARED.write_text(json.dumps({'base_image':base,'source':SOURCE,'hashes':hashes}))
    print('BRAKE_CANDIDATE_READY',flush=True)

def install(expected):
    assert re.fullmatch('[0-9a-f]{40}',expected)
    with open('/var/lock/wb-assistant-update.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        assert output('git','rev-parse','HEAD')==BASE
        assert not output('git','status','--porcelain','--untracked-files=no')
        prepared=json.loads(PREPARED.read_text());assert image()==prepared['base_image'] and prepared['source']==SOURCE
        stamp=Path('/var/backups/wb-ai-assistant/latest-verified').read_text().strip()
        assert stamp>json.loads(Path('/root/template-reuse-install-1112.json').read_text())['backup']
        spec=importlib.util.spec_from_file_location('retention','/usr/local/lib/wb-ai-assistant/backup_retention.py')
        retention=importlib.util.module_from_spec(spec);spec.loader.exec_module(retention)
        retention.verify('/var/backups/wb-ai-assistant',stamp)
        before=audit();assert before['safe_mode'] is True and before['job']['status'] in ('queued','running')
        run('git','fetch','--no-tags','origin','main');assert output('git','rev-parse','origin/main')==expected
        assert set(output('git','diff','--name-only',BASE,expected).splitlines())==set(FILES+['backend/tests/test_brake_feedback.py'])
        run('git','merge','--ff-only',expected)
        for name,sha in prepared['hashes'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha
        run('docker','compose','stop','-t','180','worker')
        run('docker','tag',IMAGE,'wb-ai-assistant-backend:latest')
        run('docker','compose','up','-d','--no-deps','backend')
        for attempt in range(90):
            try:
                health=json.load(urllib.request.urlopen('https://assistant.blackmindworks.com/api/health',timeout=5))
                assert health['status']=='ok' and health['database']=='ok'
                break
            except Exception:
                if attempt==89:raise
                time.sleep(2)
        resolved=json.loads(output('docker','compose','exec','-T','backend','python','-c',ACTIVATE,*targets()))
        run('docker','compose','up','-d','--no-deps','worker')
        for service in ('backend','worker'):
            for name,sha in prepared['hashes'].items():
                assert output('docker','compose','exec','-T',service,'sha256sum','/app/app/'+Path(name).name).split()[0]==sha
        after=audit();assert after['job']['id']==before['job']['id'] and after['safe_mode'] is True
        assert urllib.request.urlopen('https://assistant.blackmindworks.com/',timeout=10).status==200
        mount=output('docker','volume','inspect','--format','{{ .Mountpoint }}','wb-ai-assistant_wb_data')
        rotation=retention.apply('/var/backups/wb-ai-assistant',stamp,str(Path(mount)/'backups'))
        Path('/root/brake-feedback-install-1112.json').write_text(json.dumps({'installed':expected,'backup':stamp,'before':before,
            'resolved':resolved,'after':after,'health':health,'rotation_removed_bytes':rotation['remove_bytes']},ensure_ascii=False))
        print('BRAKE_FEEDBACK_INSTALLED',flush=True)
        print('resolved',len(resolved['outcomes']),'attention',after['job']['attention'],'verified',after['job']['verified'],flush=True)

if __name__=='__main__':
    os.umask(0o077)
    if sys.argv[1]=='build':build()
    else:install(sys.argv[1])
