from app.db import Session, Product, Review, set_setting
from app.main import web_research
from app.services import trusted_product_facts


def test_work_queue_separates_questions_and_respects_store_access(client):
    owner = client.get('/stores').json()['items'][0]
    ozon = client.post('/stores', json={'name': 'Ozon test', 'provider': 'ozon'}).json()
    with Session() as db:
        db.add_all([
            Product(id='queue-p1', name='Товар WB', marketplace='wb'),
            Product(id='queue-p2', name='Товар Ozon', marketplace='ozon'),
        ])
        db.flush()
        db.add_all([
            Review(id='queue-r1', wb_account_id=owner['id'], wb_review_id='queue-r1', product_id='queue-p1', rating=2, text='Плохой товар', marketplace='wb'),
            Review(id='queue-q1', wb_account_id=ozon['id'], wb_review_id='queue-q1', product_id='queue-p2', rating=0, text='Как применять?', marketplace='ozon_question'),
        ])
        db.commit()
    assert client.get('/work-queue').json()['total'] == 2
    assert client.get('/work-queue?kind=questions').json()['items'][0]['store_id'] == ozon['id']
    assert client.get('/work-queue?kind=reviews').json()['items'][0]['store_id'] == owner['id']

    user = client.post('/users', json={'username':'queue-user','display_name':'Queue User','password':'queue-password-123','position':'Менеджер Ozon'}).json()
    from app.db import User
    with Session() as db:
        db.get(User, user['id']).must_change_password = False
        db.commit()
    client.post('/auth/logout')
    logged = client.post('/auth/login', json={'username':'queue-user','password':'queue-password-123'}).json()
    client.headers['X-CSRF-Token'] = logged['csrf']
    assert client.get('/work-queue').json()['total'] == 2
    assert client.post('/stores/'+owner['id']+'/activate').status_code == 200
    assert client.post('/reviews/queue-r1/draft', json={}).status_code == 403
    assert client.post('/stores/'+ozon['id']+'/activate').status_code == 200
    assert client.post('/questions/queue-q1/draft', json={}).status_code == 403


def test_web_research_requires_owner_verification_before_reply_use(client, seeded, monkeypatch):
    async def found(product, db=None, topic='инструкция применения'):
        return [{'title': product.name, 'snippet': 'Нанести на поверхность и протереть.', 'url': 'https://example.com/product/instruction', 'host': 'example.com', 'match_score': 3}]
    monkeypatch.setattr(web_research, 'search_instructions', found)
    response = client.post('/products/p1/research')
    assert response.status_code == 200
    facts = response.json()['product']['facts']
    assert len(facts) == 1
    assert facts[0]['verification_status'] == 'WEB_UNVERIFIED'
    assert client.post('/products/p1/facts/0/verify').status_code == 400
    assert client.post('/products/p1/official-domain', json={'domain':'other.example'}).status_code == 200
    assert client.post('/products/p1/facts/0/verify').status_code == 400
    assert client.post('/products/p1/official-domain', json={'domain':'example.com'}).status_code == 200
    verified = client.post('/products/p1/facts/0/verify')
    assert verified.status_code == 200
    assert verified.json()['facts'][0]['verification_status'] == 'VERIFIED'
    assert verified.json()['facts'][0]['verified_by'] == 'owner'
    assert client.post('/products/p1/facts', json={'text':'Неофициальный факт','source':'https://shop.example/thing'}).status_code == 400


def test_only_card_and_matching_manufacturer_facts_reach_generation(client, seeded):
    with Session() as db:
        product = db.get(Product, 'p1')
        product.facts = [
            {'text':'Карточка', 'source':'Карточка товара', 'verification_status':'VERIFIED'},
            {'text':'Производитель', 'source':'https://example.com/guide', 'verification_status':'VERIFIED'},
            {'text':'Магазин', 'source':'https://shop.example/guide', 'verification_status':'VERIFIED'},
        ]
        set_setting(db, 'official_domain:p1', 'example.com')
        db.commit()
        assert [fact['text'] for fact in trusted_product_facts(db, product)] == ['Карточка', 'Производитель']
