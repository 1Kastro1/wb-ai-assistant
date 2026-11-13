import asyncio
from app.db import Session, Job
from app.pipeline_dispatch import run_slice, parallel_slice


def test_io_finishes_while_model_is_waiting_without_starting_second_model():
    with Session() as db:
        db.add_all([Job(id='model',kind='draft_recheck'),Job(id='model2',kind='draft_recheck'),
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
    fresh=(current-timedelta(seconds=40)).isoformat()
    with Session() as db:
        db.add_all([Job(id='archive',kind='review_library_audit',result={'last_served_at':old}),
            Job(id='send',kind='scheduled_publication',result={'last_served_at':old}),
            Job(id='fresh',kind='drafts',result={'fresh_reviews':True,'last_served_at':fresh})]);db.commit()
        picked=set()
        for _ in range(2):
            job=next_job(db);picked.add(job.id)
            job.result={**job.result,'last_served_at':current.isoformat()};db.commit()
        assert picked=={'archive','send'}
        assert next_job(db).id=='fresh'
