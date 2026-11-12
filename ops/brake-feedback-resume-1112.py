"""Resume the reviewed deployment with an exact, metadata-first category filter."""
import hashlib,importlib.util,json,os,sys,time,urllib.request
from pathlib import Path

FAST_SCOPE='''
def install_scope_filter(db):
    from sqlalchemy import select
    from app.db import Product
    from app.brake_feedback import RULE
    from app.owner_reply_rules import category_rule_scopes,_scope_key
    from app.shared_marketplaces import product_identity
    import app.owner_reply_rules as owner_rules
    original=owner_rules.matching_owner_rules
    target=_scope_key('category:Тормозные колодки и диски')
    literal_identities=None;memo={}
    def scoped(session,product,for_questions=False,**kwargs):
        nonlocal literal_identities
        if for_questions or kwargs:return original(session,product,for_questions,**kwargs)
        if product.id in memo:return memo[product.id]
        scopes={_scope_key('category:'+(product.category or '')),
            *(_scope_key(value) for value in category_rule_scopes(product))}
        if target in scopes:
            memo[product.id]=[RULE];return memo[product.id]
        if literal_identities is None:
            # Counterpart categories are literal in matching_owner_rules. Read
            # only id/category once; never scan every product's facts per review.
            ids=[pid for pid,category in session.execute(select(Product.id,Product.category)
                .execution_options(yield_per=1000)) if _scope_key('category:'+(category or ''))==target]
            literal_identities=set()
            for offset in range(0,len(ids),200):
                for item in session.scalars(select(Product).where(Product.id.in_(ids[offset:offset+200]))):
                    identity=product_identity(item)
                    if identity:literal_identities.add(identity)
        identity=product_identity(product)
        # Only ambiguous literal-counterpart cases use the original full query.
        # This preserves its exact brand/article/facts matching semantics.
        value=original(session,product) if identity and identity in literal_identities else []
        memo[product.id]=value
        return value
    owner_rules.matching_owner_rules=scoped
    return lambda:setattr(owner_rules,'matching_owner_rules',original)
'''

ACTIVATE='''import asyncio,json,sys
from sqlalchemy import select
from app.db import Session,Job,Review,ReviewLibraryAuditRow,Draft,Product,setting
from app.brake_feedback import upgrade_policy,recheck_cases
from app.review_library_audit import KIND,report
from app.owner_reply_policy import fingerprint
from app.owner_reply_rules import matching_owner_rules
ids=sys.argv[1:]
with Session() as db:
    job=db.scalar(select(Job).where(Job.kind==KIND).order_by(Job.created_at.desc()).limit(1));jid=job.id
    assert all(db.get(ReviewLibraryAuditRow,(jid,rid)).status in ('attention','retry','blocked','verified','verified_reference') for rid in ids)
    before={rid:db.get(Review,rid).existing_answer for rid in ids}
    restore=install_scope_filter(db)
    try:
        upgraded=upgrade_policy(db,jid);db.commit()
    finally:restore()
print('SCOPED_POLICY_READY',flush=True)
outcomes=asyncio.run(recheck_cases(jid,ids))
assert sorted(value['status'] for value in outcomes.values())==['verified','verified_reference']
with Session() as db:
    for rid in ids:
        row=db.get(ReviewLibraryAuditRow,(jid,rid));r=db.get(Review,rid)
        assert row.status in ('verified','verified_reference') and row.template_id
        assert r.existing_answer==before[rid] and r.safety_critical is True and r.risk=='NORMAL'
        if not r.is_answered:
            d=db.scalar(select(Draft).where(Draft.review_id==rid));p=db.get(Product,r.product_id)
            assert d.quality['passed'] and d.quality['owner_rules']['passed'] and d.quality['owner_rules']['checked']
            assert d.quality['owner_rules']['fingerprint']==fingerprint(matching_owner_rules(db,p))
            assert not d.quality['owner_rules'].get('issues') and not d.quality['owner_rules'].get('technical_error')
    schedule=setting(db,'automation_schedule',{})
    assert setting(db,'safe_mode',False) is True
    assert not any(schedule.get(k) for k in ('enabled','reviews_sync_enabled','questions_sync_enabled','drafts_enabled','question_drafts_enabled','publication_enabled'))
    print('RESOLVED_JSON '+json.dumps({'upgraded':upgraded,'outcomes':outcomes,'audit':report(db)},ensure_ascii=False),flush=True)
'''


def main(expected):
    import fcntl
    os.umask(0o077)
    assert expected=='2ae41a30b90db16e8efb678bb8f5cf998b8300c5'
    path=Path('/root/bf.py')
    assert hashlib.sha256(path.read_bytes()).hexdigest()=='71dd3c2e7418f2730e2d754f6182317b341310aa16985166bc9c7fbe741b54d9'
    spec=importlib.util.spec_from_file_location('base_install',path)
    base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
    target=Path('/root/brake-feedback-install-1112.json')
    with open('/var/lock/wb-assistant-update.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if target.exists():
            saved=json.loads(target.read_text());assert saved['installed']==expected
            print('BRAKE_FEEDBACK_ALREADY_INSTALLED',flush=True);return
        assert base.output('git','rev-parse','HEAD')==expected
        assert not base.output('git','status','--porcelain','--untracked-files=no')
        prepared=json.loads(base.PREPARED.read_text())
        assert prepared['source']==base.SOURCE
        assert base.image()==base.output('docker','image','inspect','--format','{{ .Id }}',base.IMAGE)
        for name,sha in prepared['hashes'].items():
            assert hashlib.sha256((base.ROOT/name).read_bytes()).hexdigest()==sha
            assert base.output('docker','compose','exec','-T','backend','sha256sum','/app/app/'+Path(name).name).split()[0]==sha
        stamp=Path('/var/backups/wb-ai-assistant/latest-verified').read_text().strip()
        assert stamp>json.loads(Path('/root/template-reuse-install-1112.json').read_text())['backup']
        rs=importlib.util.spec_from_file_location('retention','/usr/local/lib/wb-ai-assistant/backup_retention.py')
        retention=importlib.util.module_from_spec(rs);rs.loader.exec_module(retention)
        retention.verify('/var/backups/wb-ai-assistant',stamp)
        base.run('docker','compose','stop','-t','180','worker')
        before=base.audit()
        raw=base.output('docker','compose','exec','-T','backend','python','-c',FAST_SCOPE+'\n'+ACTIVATE,*base.targets())
        print('SCOPED_CASES_VERIFIED',flush=True)
        resolved=json.loads(next(line[len('RESOLVED_JSON '):] for line in raw.splitlines() if line.startswith('RESOLVED_JSON ')))
        base.run('docker','compose','up','-d','--no-deps','worker')
        for service in ('backend','worker'):
            for name,sha in prepared['hashes'].items():
                assert base.output('docker','compose','exec','-T',service,'sha256sum','/app/app/'+Path(name).name).split()[0]==sha
        health=json.load(urllib.request.urlopen('https://assistant.blackmindworks.com/api/health',timeout=10))
        assert health['status']=='ok' and health['database']=='ok'
        assert urllib.request.urlopen('https://assistant.blackmindworks.com/',timeout=10).status==200
        after=base.audit();assert after['job']['id']==before['job']['id'] and after['safe_mode'] is True
        mount=base.output('docker','volume','inspect','--format','{{ .Mountpoint }}','wb-ai-assistant_wb_data')
        rotation=retention.apply('/var/backups/wb-ai-assistant',stamp,str(Path(mount)/'backups'))
        target.write_text(json.dumps({'installed':expected,'backup':stamp,'before':before,'resolved':resolved,
            'after':after,'health':health,'rotation_removed_bytes':rotation['remove_bytes']},ensure_ascii=False))
        print('BRAKE_FEEDBACK_INSTALLED',flush=True)
        print('resolved',len(resolved['outcomes']),'attention',after['job']['attention'],'verified',after['job']['verified'],flush=True)

if __name__=='__main__':main(sys.argv[1])
