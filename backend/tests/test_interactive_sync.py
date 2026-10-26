import asyncio
from unittest.mock import AsyncMock
from sqlalchemy import select
from app import main
from app.db import Account, Draft, Job, Product, Review, Session, StoreProfile
from app.integrations import wb


def test_single_question_runs_before_import_finishes(client, monkeypatch):
    events = []
    with Session() as db:
        db.add(Product(id='interactive-product', name='Очиститель'))
        db.flush()
        db.add(Review(id='interactive-q', wb_review_id='question:q', product_id='interactive-product', wb_account_id='owner', marketplace='wb_question', text='Как применять?', rating=0))
        db.add_all([
            Job(id='catalog', kind='products', priority=10, result={'account_id': 'owner'}),
        ])
        db.commit()

    async def generate(db, review_id, instruction=''):
        events.append('answer')
        draft = Draft(id='interactive-answer', review_id=review_id, text='Ответ', original='Ответ')
        db.add(draft)
        db.commit()
        return {'id': draft.id}

    class Provider:
        async def sync_products(self, db, account_id, *args, between_pages=None, **kwargs):
            events.append('page-one')
            db.add(Job(id='single', kind='single_question_draft', priority=40, result={'account_id':'owner', 'selected_ids':['interactive-q']}))
            db.commit()
            assert between_pages is not None
            await between_pages()
            with Session() as read:
                assert read.get(Draft, 'interactive-answer') is not None
            events.append('page-two')
            return 200

    monkeypatch.setattr(main, 'generate_draft', generate)
    monkeypatch.setattr(main, 'provider_for', lambda store: Provider())
    asyncio.run(main.run_job_queue())
    assert events == ['page-one', 'answer', 'page-two']
    with Session() as db:
        assert db.get(Job, 'catalog').status == 'completed'
        assert db.get(Job, 'single').status == 'completed'


def test_wb_large_question_page_commits_before_interactive_yield(client, monkeypatch):
    async def pages(*args, **kwargs):
        if not args[1]:
            yield ([{'id': str(i), 'text': 'Как применять?', 'productDetails': {'nmId': 42, 'productName': 'Средство'}} for i in range(120)], {'skip': 5000})

    observed = []
    async def interactive():
        with Session() as read:
            observed.append(len(list(read.scalars(select(Review.id).where(Review.marketplace == 'wb_question')))))

    monkeypatch.setattr('app.integrations.wb_question_pages', pages)
    monkeypatch.setattr('app.integrations.wb_token', lambda *args: 'test-token')
    with Session() as db:
        assert asyncio.run(wb.sync_questions(db, 'owner', between_pages=interactive)) == 120
    assert observed == [100]


def test_interactive_yield_does_not_start_recursive_imports(client, monkeypatch):
    with Session() as db:
        db.add_all([
            Job(id='queued-import', kind='questions', priority=1, result={'account_id': 'owner'}),
            Job(id='interactive', kind='single_draft', priority=40, result={'account_id': 'owner'}),
        ])
        db.commit()
    execute = AsyncMock()
    monkeypatch.setattr(main, 'execute_job', execute)
    asyncio.run(main.run_waiting_interactive())
    execute.assert_awaited_once_with('interactive')


def test_explicit_sync_store_cannot_drift_after_another_tab_switches(client, monkeypatch):
    monkeypatch.setattr(main, 'run_job_queue', AsyncMock())
    with Session() as db:
        db.add(StoreProfile(id='market', provider='yandex', name='Market'))
        db.add(Account(id='market', encrypted_token='unused'))
        db.commit()
    first = client.post('/jobs/sync/products?store_id=market')
    assert first.status_code == 200
    assert first.json()['result']['account_id'] == 'market'
    assert client.post('/jobs/sync/products?store_id=market').json()['id'] == first.json()['id']
    assert client.post('/jobs/sync/products?store_id=missing').status_code == 404
