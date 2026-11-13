from sqlalchemy import select
from app.db import Session,Job,Account,StoreProfile,setting,set_setting
from app.pipeline_activation import activate,VERSION


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
