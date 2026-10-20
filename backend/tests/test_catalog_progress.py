import asyncio
from types import SimpleNamespace
from app import integrations, main
from app.db import Session


def test_empty_interactive_checkpoint_yields_to_http_tasks(client):
    async def scenario():
        seen = []
        asyncio.get_running_loop().call_soon(seen.append, 'http')
        await main.run_waiting_interactive()
        assert seen == ['http']
    asyncio.run(scenario())


def test_yandex_archive_checkpoint_resumes_without_replaying_active_catalog(client, monkeypatch):
    calls, checkpoints = [], []
    async def request(*args, **kwargs):
        calls.append(kwargs)
        page = kwargs.get('params', {}).get('pageToken')
        payload = {'offerMappings': [], 'paging': {}}
        if len(calls) == 1:
            payload['paging']['nextPageToken'] = 'archive-next'
        return {'result': payload}
    async def cards(*args, **kwargs):
        return {}
    monkeypatch.setattr(integrations, 'store_credentials', lambda *a: (SimpleNamespace(id='test', config={'business_id':'1'}), 'token'))
    monkeypatch.setattr(integrations.guard, 'request', request)
    monkeypatch.setattr('app.yandex_content.card_characteristics', cards)
    with Session() as db:
        count = asyncio.run(integrations.YandexProvider().sync_products(
            db, 'test', resume={'phase':'archived','page':'archive-start','processed':14300},
            checkpoint=checkpoints.append))
    assert count == 14300
    assert calls[0]['params']['pageToken'] == 'archive-start'
    assert all(call['body'] == {'archived': True} for call in calls)
    assert checkpoints[0] == {'phase':'archived','page':'archive-next','processed':14300}
    assert checkpoints[-1]['phase'] == 'missing'


def test_yandex_missing_phase_does_not_restart_catalog(client, monkeypatch):
    async def unexpected(*a, **kw):
        raise AssertionError('Catalog must not restart')
    monkeypatch.setattr(integrations, 'store_credentials', lambda *a: (SimpleNamespace(id='empty', config={'business_id':'1'}), 'token'))
    monkeypatch.setattr(integrations.guard, 'request', unexpected)
    with Session() as db:
        assert asyncio.run(integrations.YandexProvider().sync_products(
            db, 'empty', resume={'phase':'missing','processed':15000})) == 15000
