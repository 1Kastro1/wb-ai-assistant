from sqlalchemy import select
from app.db import Session,Job,Account,StoreProfile,setting,set_setting
from app.pipeline_activation import activate,VERSION


def test_consolidation_retains_scope_union_progress_and_explicit_manual_checks():
    from app.pipeline_activation import consolidate_checks
    scopes=[{'id':'a','kind':'REVIEW_RULE','scope':'product:p','text':'A'},
        {'id':'b','kind':'QUESTION_RULE','scope':'brand:B','text':'B'}]
    with Session() as db:
        db.add(StoreProfile(id='owner',provider='wb',name='Shop'))
        for i in range(2):
            db.add(Job(id=str(i),kind='draft_recheck',result={'account_id':'owner','rule_change':True,
                'policy_scan':True,'check_only':True,'cursor_id':'kept-'+str(i),'checked':100,
                'rule_scopes':[scopes[i]]}))
        db.add(Job(id='manual',kind='draft_recheck',result={'account_id':'owner','manual_check':True,'retry_draft_id':'d'}))
        db.commit()
        assert consolidate_checks(db)==2;db.commit()
        target=db.get(Job,db.get(Job,'0').result['superseded_by'])
        assert target.result['rule_scopes']==scopes
        for i in range(2):
            old=db.get(Job,str(i))
            assert old.status=='cancelled' and old.result['cursor_id']=='kept-'+str(i) and old.result['checked']==100
            assert old.result['superseded_by']==target.id
        assert db.get(Job,'manual').status=='queued'


def test_explicit_activation_preserves_consent_switches_intervals_and_checkpoints():
    with Session() as db:
        db.add(StoreProfile(id='owner',provider='wb',name='Shop'))
        db.add(Account(id='owner',encrypted_token='test-only'))
        before={'enabled':True,'drafts_enabled':False,'reviews_sync_enabled':False,
            'questions_sync_enabled':False,'question_drafts_enabled':False,'publication_enabled':False,
            'reviews_hours':5,'drafts_hours':4,'publication_markets':{'wb':False,'ozon':True,'yandex':False}}
        set_setting(db,'automation_schedule',before);set_setting(db,'safe_mode',True)
        db.add(Job(id='old',kind='drafts',result={'account_id':'owner','scheduled':True,'since':'','processed':200,'cursor':'saved'}))
        db.add(Job(id='audit',kind='review_library_audit',result={'checked':77,'stage':1}))
        db.commit()
        activate(db);db.commit()
        schedule=setting(db,'automation_schedule')
        assert schedule['reviews_sync_enabled'] and schedule['drafts_enabled']
        assert not schedule['questions_sync_enabled'] and not schedule['question_drafts_enabled']
        assert not schedule['publication_enabled'] and schedule['publication_markets']==before['publication_markets']
        assert schedule['reviews_hours']==5 and schedule['drafts_hours']==4 and setting(db,'safe_mode') is True
        old=db.get(Job,'old')
        assert old.result['processed']==200 and old.result['cursor']=='saved' and old.result['archive']
        assert db.get(Job,'audit').result=={'checked':77,'stage':1,'fair_markets':True}
        count=len(list(db.scalars(select(Job))))
        assert activate(db)=={'already_applied':True}
        assert len(list(db.scalars(select(Job))))==count
        assert setting(db,VERSION+'-backup')['schedule']['publication_enabled'] is False
