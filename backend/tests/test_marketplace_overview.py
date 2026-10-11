from datetime import datetime, timedelta, timezone

from app.db import Draft, Product, Review, Session, User


def test_marketplace_overview_counts_reviews_and_questions_separately(client):
    wb_store = client.get('/stores').json()['items'][0]
    ozon = client.post('/stores', json={'name': 'Ozon shop', 'provider': 'ozon'}).json()
    yandex = client.post('/stores', json={'name': 'Market shop', 'provider': 'yandex'}).json()
    recent = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with Session() as db:
        for store, product_id in ((wb_store, 'overview-wb'), (ozon, 'overview-ozon'), (yandex, 'overview-yandex')):
            db.add(Product(id=product_id, name='Товар', brand='', category='', marketplace=store['provider']))
        db.flush()
        db.add_all([
            Review(id='overview-r-wb', wb_account_id=wb_store['id'], wb_review_id='overview-r-wb', product_id='overview-wb', rating=2, text='Не подошло', created_at=recent, is_answered=False, status='draft_ready', topics=['жалоба'], marketplace='wb'),
            Review(id='overview-r-ozon', wb_account_id=ozon['id'], wb_review_id='overview-r-ozon', product_id='overview-ozon', rating=5, text='Хорошо', created_at=recent, is_answered=True, status='published', marketplace='ozon'),
            Review(id='overview-q-ozon', wb_account_id=ozon['id'], wb_review_id='question:1', product_id='overview-ozon', rating=0, text='Как применять?', created_at=recent, is_answered=False, status='unanswered', topics=['вопрос'], marketplace='ozon_question'),
            Review(id='overview-r-yandex', wb_account_id=yandex['id'], wb_review_id='overview-r-yandex', product_id='overview-yandex', rating=3, text='Нормально', created_at=old, is_answered=False, status='unanswered', marketplace='yandex'),
        ])
        db.flush()
        db.add(Draft(id='overview-draft', review_id='overview-r-wb', text='Черновик', original='Черновик'))
        db.commit()

    data = client.get('/analytics/marketplaces').json()
    assert len(data['stores']) == 3
    assert data['totals']['reviews'] == 3
    assert data['totals']['questions'] == 1
    assert data['totals']['reviews_negative'] == 2
    assert data['totals']['reviews_drafts'] == 1
    assert len(data['trend']) == 14
    assert data['trend'][-1]['reviews'] == 2
    assert data['trend'][-1]['questions'] == 1
    by_provider = {store['provider']: store for store in data['stores']}
    assert by_provider['ozon']['reviews_answered'] == 1
    assert by_provider['ozon']['questions_unanswered'] == 1
    assert by_provider['yandex']['reviews'] == 1
    assert client.get('/analytics/marketplaces?days=30').json()['totals']['reviews'] == 2
    assert client.get('/analytics').json()['topics'] == {'жалоба': 1}

    member = client.post('/users', json={'username': 'ozon.manager', 'display_name': 'Ozon manager', 'password': 'long-test-password-123', 'position': 'Менеджер Ozon'}).json()
    assert client.patch('/users/' + member['id'], json={'store_ids': [ozon['id']]}).status_code == 200
    with Session() as db:
        user = db.get(User, member['id'])
        user.must_change_password = False
        db.commit()
    assert client.post('/auth/logout').status_code == 200
    login = client.post('/auth/login', json={'username': 'ozon.manager', 'password': 'long-test-password-123'})
    assert login.status_code == 200
    client.headers['X-CSRF-Token'] = login.json()['csrf']
    restricted = client.get('/analytics/marketplaces').json()
    assert [store['id'] for store in restricted['stores']] == [wb_store['id'], ozon['id'], yandex['id']]
    assert [store['accessible'] for store in restricted['stores']] == [False, True, False]
    assert restricted['totals']['reviews'] == 3
    assert restricted['totals']['questions'] == 1
    assert restricted['trend'][-1]['reviews'] == 2
    assert client.post('/stores/'+wb_store['id']+'/activate').status_code == 403
