from app.db import Session, Job, now, set_setting


def test_health_distinguishes_api_from_background_worker(client):
    assert client.get('/health').json()['status'] == 'ok'
    assert client.get('/health/background').json()['worker'] == 'unavailable'
    with Session() as db:
        set_setting(db, 'worker_heartbeat', {'at': now(), 'scheduler': 'ok', 'model': 'ok'})
        db.commit()
    assert client.get('/health/background').json()['status'] == 'ok'
    with Session() as db:
        db.add(Job(id='stalled', kind='review_minion', status='running',
            created_at='2020-01-01T00:00:00+00:00'))
        db.commit()
    assert client.get('/health/background').json()['queue'] == 'stalled'
