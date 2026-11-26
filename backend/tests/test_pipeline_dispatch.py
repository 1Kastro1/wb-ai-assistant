import asyncio
import pytest
from app.db import Session, Job, StoreProfile
from app.pipeline_dispatch import run_slice, parallel_slice


@pytest.mark.parametrize('kind', ['draft_recheck', 'drafts', 'question_drafts'])
def test_io_finishes_while_model_is_waiting_without_starting_second_model(kind):
    with Session() as db:
        db.add_all([Job(id='model',kind=kind),Job(id='model2',kind='draft_recheck'),
            Job(id='sync',kind='reviews'),Job(id='send',kind='scheduled_publication')]);db.commit()
    async def scenario():
        io_finished=asyncio.Event();seen=[]
        async def execute(jid):
            seen.append(jid)
            with Session() as db:db.get(Job,jid).status='running';db.commit()
            if jid=='model':await asyncio.wait_for(io_finished.wait(),2)
            else:
                await asyncio.sleep(0)
                io_finished.set()
            with Session() as db:db.get(Job,jid).status='completed';db.commit()
        await run_slice('model',execute,poll_seconds=.01)
        assert seen[0]=='model' and len(seen)>=2 and 'model2' not in seen
        assert not parallel_slice.get()
    asyncio.run(scenario())


def test_cancellation_waits_for_io_cleanup_before_releasing_caller():
    with Session() as db:
        db.add_all([Job(id='model',kind='draft_recheck'),Job(id='sync',kind='reviews')]);db.commit()
    async def scenario():
        started=asyncio.Event();finished=[]
        async def execute(jid):
            try:
                if jid=='sync':started.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.01)
                finished.append(jid)
        task=asyncio.create_task(run_slice('model',execute,poll_seconds=.01))
        await asyncio.wait_for(started.wait(),2)
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        assert set(finished)=={'model','sync'}
        assert not parallel_slice.get()
    asyncio.run(scenario())


def test_verification_retry_budget_is_shared_by_exact_input():
    from app.rule_retry import checkpoint_verdict
    failure={'technical_error':True,'checked':False,'passed':False,'retryable':True}
    previous={}
    for _ in range(5):previous=checkpoint_verdict(failure,previous,'same','now')
    assert previous['retryable'] is False and previous['failure_code']=='retry_exhausted'
    assert checkpoint_verdict(failure,previous,'changed','now')['check_failures']==1
    assert checkpoint_verdict(failure,previous,'same','now',manual=True)['check_failures']==1
    continued=checkpoint_verdict({**failure,'continuation':True},previous,'same','now')
    assert continued['check_failures']==0 and continued['retryable'] is True


def test_continuous_fresh_batches_cannot_starve_archive_or_confirmed_sends():
    from datetime import datetime,timezone,timedelta
    from app.queue_policy import next_job
    current=datetime.now(timezone.utc)
    old=(current-timedelta(minutes=10)).isoformat()
    fresh=(current-timedelta(seconds=90)).isoformat()
    with Session() as db:
        db.add_all([Job(id='archive',kind='review_library_audit',result={'last_served_at':old}),
            Job(id='send',kind='scheduled_publication',result={'last_served_at':old}),
            Job(id='fresh',kind='drafts',result={'fresh_reviews':True,'last_served_at':fresh})]);db.commit()
        assert next_job(db).id == 'fresh'
        picked=[]
        for _ in range(3):
            job=next_job(db);picked.append(job.id)
            job.result={**job.result,'last_served_at':current.isoformat()};db.commit()
        assert picked[0] == 'fresh'
        assert set(picked[1:]) == {'archive','send'}


def test_complete_validated_save_has_an_approval_event_but_pending_or_failed_check_does_not():
    from app.owner_reply_policy import attach_rule_quality
    proof={'passed':True,'checked':True,'issues':[],'checked_at':'2000-01-01'}
    result=attach_rule_quality({'passed':True},proof)
    assert result['owner_rules']['checked_at']>'2000-01-01'
    assert proof['checked_at']=='2000-01-01'  # the source template proof stays intact
    for blocked in ({'passed':False,'checked':False,'issues':[]},
                    {'passed':False,'checked':False,'technical_error':True,'issues':['timeout']}):
        result=attach_rule_quality({'passed':True},blocked)
        assert 'checked_at' not in result['owner_rules'] and not result['passed']


def test_publication_slices_overlap_across_markets_but_not_within_a_market():
    stores = {'first': 'wb', 'another-wb': 'wb', 'ozon': 'ozon', 'yandex': 'yandex'}
    with Session() as db:
        db.add_all(StoreProfile(id=store, name=store, provider=provider)
                   for store, provider in stores.items())
        db.add_all(Job(id=store, kind='scheduled_publication', result={'account_id': store})
                   for store in stores)
        db.add(Job(id='yandex-backoff', kind='scheduled_publication', priority=1,
                   result={'account_id': 'yandex', 'retry_at': 4102444800}))
        db.commit()

    async def scenario():
        started = set()
        all_started = asyncio.Event()

        async def execute(job_id):
            started.add(job_id)
            if len(started) == 3:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), timeout=2)

        await run_slice('first', execute)
        assert started == {'first', 'ozon', 'yandex'}

    asyncio.run(scenario())
