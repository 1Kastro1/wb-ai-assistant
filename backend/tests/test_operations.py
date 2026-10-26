import asyncio
import time
from datetime import datetime, timedelta, timezone
from app import main, batch_processing
from app.db import Session, Product, Review, Draft, Job, Setting, set_setting
from app.queue_policy import next_job
from app.safety import classify


def test_background_round_robin_does_not_starve_imports_and_interactive_wins(client):
    with Session() as db:
        db.add_all([Job(id='audit', kind='draft_audit', priority=5, created_at='2026-01-01', result={}),
                    Job(id='sync', kind='reviews', priority=200, created_at='2026-01-02', result={}),
                    Job(id='click', kind='single_draft', priority=40, created_at='2026-01-03', result={}),
                    Job(id='retry', kind='questions', priority=1, created_at='2025-01-01', result={'retry_at': time.time()+60})])
        db.commit()
        assert next_job(db).id == 'click'
        db.get(Job, 'click').status = 'completed'; db.commit()
        assert next_job(db).id == 'audit'
        db.get(Job, 'audit').result = {'last_served_at':'2026-01-04'}; db.commit()
        assert next_job(db).id == 'sync'


def test_catalog_filters_cover_whole_catalog_and_respect_trust(client):
    with Session() as db:
        for index in range(70):
            db.add(Product(id=f'z{index:03}', name=f'Товар {index:03}', marketplace='wb', facts=[]))
        db.flush()
        for index in range(70):
            db.add(Review(id=f'r{index}', wb_review_id=f'r{index}', wb_account_id='owner', product_id=f'z{index:03}', rating=2, text=''))
        db.get(Product,'z069').facts=[{'kind':'instruction','verification_status':'VERIFIED','source':'https://brand.test/instruction','text':'Инструкция'}]
        db.get(Product,'z068').facts=[{'kind':'instruction','verification_status':'VERIFIED','source':'https://brand.test.evil/instruction','text':'Нельзя доверять'}]
        set_setting(db,'official_domain:z069','brand.test');set_setting(db,'official_domain:z068','brand.test')
        db.commit()
    ready=client.get('/products/page?knowledge=ready').json()
    assert ready['total']==1 and ready['items'][0]['id']=='z069'
    missing=client.get('/products/page?knowledge=missing&limit=60').json()
    assert missing['total']==69 and missing['has_more']
    assert client.get('/products/page?knowledge=priority').json()['total']==69
    assert client.get('/products/page?knowledge=invalid').status_code==422


def test_store_counts_and_queue_ready_are_separate_from_saved_drafts(client):
    with Session() as db:
        db.add(Product(id='op',name='Товар'));db.flush()
        for rid,market,status,manual,age,quality in [('new','wb','draft_ready',False,0,True),('old','wb','manual_review',True,10,False),('question','wb_question','unanswered',False,0,None)]:
            db.add(Review(id=rid,wb_review_id=rid,product_id='op',marketplace=market,status=status,manual=manual,
                rating=0 if market.endswith('_question') else 5,text='',created_at=(datetime.now(timezone.utc)-timedelta(days=age)).isoformat()))
            db.flush()
            if quality is not None:db.add(Draft(id='d'+rid,review_id=rid,text='Ответ',original='Ответ',quality={'passed':quality,'issues':[] if quality else ['Нужно проверить']}))
        db.commit()
    store=client.get('/stores').json()['items'][0]
    assert store['reviews']==2 and store['questions']==1
    assert client.get('/work-queue?view=ready').json()['total']==1
    assert client.get('/work-queue?view=recent').json()['total']==2
    assert client.get('/work-queue?view=backlog').json()['total']==1
    item=client.get('/work-queue?view=manual').json()['items'][0]
    assert not item['verification_passed'] and item['reasons']


def test_negated_faults_do_not_hide_real_danger():
    for text in ['Хорошие колодки. Не скрипят и не сильно пылят.', 'Нет ни биения, ни вибраций. Перегрева не чувствую.', 'Диск не перегревается, скрежета нет.']:
        assert not classify(text,'тормозные колодки')['manual'],text
    for text in ['Плохо тормозит', 'Не тормозит, утечка жидкости', 'Нет вибраций, но после установки стало хуже тормозить', 'Сильные вибрации при торможении']:
        assert classify(text,'тормозные колодки')['manual'],text


def test_quality_excludes_automation_edits_from_owner_rate(client):
    from app.services import QUALITY_EVALUATOR_VERSION
    with Session() as db:
        db.add(Product(id='q',name='Товар'));db.flush()
        for rid,actor in [('a','automation'),('u','owner')]:
            db.add(Review(id=rid,wb_review_id=rid,product_id='q',rating=5,text='Отлично'));db.flush()
            db.add(Draft(id=rid,review_id=rid,text='Ответ',original='Ответ',edits=[{'by':actor}],quality={'passed':True,'score':99,'evaluator_version':QUALITY_EVALUATOR_VERSION}))
        db.commit()
    report=client.get('/quality-report').json()
    assert report['drafts']==2 and report['edited']==1 and report['edit_rate']==50
    assert report['automated_changes']==1


def test_sync_incident_clears_after_success_and_jobs_show_wait_reason(client):
    with Session() as db:
        db.add_all([Job(id='old-error',kind='products',status='failed',created_at='2026-01-01',result={'account_id':'owner','message':'HTTP 500'}),
                    Job(id='new-success',kind='products',status='completed',created_at='2026-01-02',result={'account_id':'owner'}),
                    Job(id='running',kind='drafts',status='running',result={'account_id':'owner'}),
                    Job(id='waiting',kind='questions',status='queued',result={'account_id':'owner'})]);db.commit()
        snapshot=main.system_status({'role':'owner','user_id':'owner'},db)
        assert not snapshot['sync_incidents']
        assert snapshot['running_jobs']==1 and snapshot['queued_jobs']==1
        assert next(t for t in snapshot['tasks'] if t['id']=='waiting')['blocking_job']=='running'


def test_long_batch_yields_only_after_committed_answers_and_keeps_counters(client,seeded,monkeypatch):
    async def generate(db,rid,instruction=''):
        await asyncio.sleep(.015)
        draft=Draft(id='sliced-'+rid,review_id=rid,text='Ответ',original='Ответ');db.add(draft);db.commit()
        return {'id':draft.id}
    async def nothing():pass
    monkeypatch.setattr(batch_processing,'BATCH_SLICE_SECONDS',.01)
    with Session() as db:
        db.add_all([Job(id='slice',kind='drafts',status='running',result={'account_id':'owner'}),Job(id='other',kind='draft_audit',result={})]);db.commit()
    result,_=asyncio.run(batch_processing.run_draft_batch('slice',generate,nothing))
    assert result['yielded'] and 0<result['processed']<4 and result['remaining']>0
    with Session() as db:
        assert db.get(Job,'slice').result['processed']==result['processed']
