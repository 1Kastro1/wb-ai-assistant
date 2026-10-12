import asyncio

from app.db import Job, Session, StoreProfile
from app import main


def test_sync_resumes_from_committed_checkpoint(client, monkeypatch):
    with Session() as db:
        db.add(StoreProfile(id='sync-test', name='Проверка', provider='wb'))
        db.add(Job(id='resume-job', kind='products', result={'account_id': 'sync-test'}, max_attempts=2))
        db.commit()

    starts = []

    class Provider:
        async def sync_products(self, db, account_id, progress, cancelled, resume=None, checkpoint=None):
            starts.append(resume)
            if len(starts) == 1:
                checkpoint({'skip': 5, 'processed': 5})
                raise RuntimeError('Временная ошибка сети')
            assert resume == {'skip': 5, 'processed': 5}
            checkpoint({'skip': 10, 'processed': 10})
            return 10

    monkeypatch.setattr(main, 'provider_for', lambda store: Provider())
    asyncio.run(main.run_sync_job('resume-job'))
    with Session() as db:
        first = db.get(Job, 'resume-job')
        assert first.status == 'queued'
        assert first.result['checkpoint']['processed'] == 5

    asyncio.run(main.run_sync_job('resume-job'))
    with Session() as db:
        second = db.get(Job, 'resume-job')
        assert second.status == 'completed'
        assert second.result['processed'] == 10
    status = client.get('/sync/status')
    assert status.status_code == 200
    assert next(item for item in status.json()['stores'] if item['store_id'] == 'sync-test')['kinds']['products']['last_success_count'] == 10
