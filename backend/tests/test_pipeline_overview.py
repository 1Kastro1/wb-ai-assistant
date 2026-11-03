from sqlalchemy import select
from app.db import Session,Product,Review,Draft,Job,Publication,StoreProfile,GenerationOutcome,set_setting
from app.queue_overview import queue_overview
from app.generation_control import record_outcome,batch_context


def seed(db):
    set_setting(db,'automation_schedule',{'enabled':True,'drafts_enabled':True,'publication_enabled':True,
        'publication_low_ratings':True,'publication_all_low_ratings':True,
        'publication_markets':{'wb':True,'ozon':True,'yandex':True}})
    db.add(Product(id='p',name='Товар'));db.flush()
    cases=[('negative',1,'manual_review',False,'NORMAL',{'passed':True,'owner_rules':{'checked':True,'passed':True}}),
        ('technical',5,'manual_review',False,'NORMAL',{'passed':False,'owner_rules':{'technical_error':True,'checked':False,'passed':False}}),
        ('pending',5,'manual_review',False,'NORMAL',{'passed':False,'owner_rules':{'checked':False,'status':'pending'}}),
        ('danger',1,'manual_review',True,'HIGH',{'passed':False,'owner_rules':{'checked':False}}),
        ('content',5,'manual_review',False,'NORMAL',{'passed':False,'owner_rules':{'checked':True,'violations':['Нет ответа по существу']}})]
    for rid,rating,status,manual,risk,quality in cases:
        db.add(Review(id=rid,wb_review_id=rid,product_id='p',text='Текст',rating=rating,
            status=status,manual=manual,risk=risk));db.flush()
        db.add(Draft(id=rid,review_id=rid,text='Ответ',original='Ответ',quality=quality))
    db.add(Review(id='sent',wb_review_id='sent',product_id='p',rating=1,text='',is_answered=True));db.flush()
    db.add(Publication(id='publication',review_id='sent',action_id='old',status='published'))
    db.add(Job(id='refresh',kind='draft_recheck',result={'account_id':'owner','policy_regeneration':True,
        'total':100,'processed':21,'saved':20,'preserved':1,'updated_at':'2026-11-01T10:00:00+00:00'}))
    db.add(Job(id='cancel',kind='drafts',cancel_requested=True,result={'account_id':'owner'}))
    db.commit()


def test_counts_separate_negative_approval_personal_decisions_and_outages(client):
    with Session() as db:
        seed(db)
        data=queue_overview(db,{'role':'owner'},'owner')
        store=next(x for x in data['market_processes'] if x['id']=='owner')
        assert store['counts']['ready']==1 and store['counts']['negative_ready']==1
        assert store['counts']['published']==1 and store['counts']['negative_published']==1
        assert store['counts']['personal']==2 and store['counts']['negative_personal']==1
        assert store['counts']['technical']==1 and store['counts']['pending']==1
        assert store['refresh']['remaining']==79 and store['refresh']['usage']=={}
        assert store['refresh']['last_progress_at'] is None  # no invented historical timestamp
        assert 'cancel' not in [t['id'] for t in data['tasks']]
    result=client.get('/work-queue?view=attention').json()
    assert {r['id'] for r in result['items']}=={'danger','content'}
    negative=next(r for r in client.get('/work-queue?view=all').json()['items'] if r['id']=='negative')
    assert negative['auto_publication'] and not negative['manual']


def test_empty_store_and_employee_scope_are_preserved(client):
    with Session() as db:
        seed(db);db.add(StoreProfile(id='other',provider='ozon',name='Другой'));db.commit()
        owner=queue_overview(db,{'role':'owner'},'owner')['market_processes']
        assert next(s for s in owner if s['id']=='other')['counts']['ready']==0
        scoped=queue_overview(db,{'role':'operator'},'owner')['market_processes']
        assert [s['id'] for s in scoped]==['owner']


def test_usage_is_atomic_and_idempotent_with_saved_outcome(client):
    with Session() as db:
        seed(db);draft=db.get(Draft,'negative');draft.model='verified-template-1.0';draft.revision=2
        review=db.get(Review,'negative');token=batch_context.set({'job_id':'refresh'})
        try:
            record_outcome(db,review,draft);db.rollback()
            assert not db.get(Job,'refresh').result.get('usage')
            draft=db.get(Draft,'negative');draft.model='verified-template-1.0';draft.revision=2
            record_outcome(db,review,draft);db.commit()
            record_outcome(db,review,draft);db.commit()
        finally:batch_context.reset(token)
        usage=db.get(Job,'refresh').result['usage']
        assert usage['template']==1 and usage['updated']==1 and not usage.get('created')
        assert db.get(Job,'refresh').result['last_progress_at']
        assert db.get(GenerationOutcome,('refresh','negative'))


def test_recent_saved_result_is_progress_even_while_large_job_is_not_finished(client):
    with Session() as db:
        seed(db)
        stamp='2026-11-03T17:55:00+00:00'
        db.add(GenerationOutcome(job_id='ongoing-job',review_id='negative',created_at=stamp));db.commit()
        result=next(s for s in queue_overview(db,{'role':'owner'},'owner')['market_processes'] if s['id']=='owner')
        assert result['last_progress_at']==stamp
        assert result['refresh']['last_progress_at'] is None
