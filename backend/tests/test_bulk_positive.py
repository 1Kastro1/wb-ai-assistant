import pytest
from sqlalchemy import select
from app import bulk_publication as bulk
from app.db import Session, Action, Review, Draft, Publication, StoreProfile
from test_bulk_publication import prepared, finish


def collect(client, scope='positive'):
    response = client.post('/publications/wb/all', json={
        'store_id': 'owner', 'include_excluded': True, 'rating_scope': scope})
    assert response.status_code == 200, response.text
    aid = response.json()['id']
    with Session() as db:
        jid = db.get(Action, aid).payload['prepare_job_id']
    bulk.prepare_step(jid)
    return aid


def test_positive_batch_omits_low_ratings_complaints_and_risks_but_handles_negation(client, prepared):
    with Session() as db:
        for i, rating in enumerate((1, 2, 3)):
            db.get(Review, f'bulk-{i:04}').rating = rating
        db.get(Review, 'bulk-0003').text = 'Работает отлично, но упаковка повреждена'
        db.get(Review, 'bulk-0004').risk = 'HIGH'
        db.get(Review, 'bulk-0005').text = 'Всё в порядке, электролит не протёк. Продавцу отлично.'
        db.get(Review, 'bulk-0006').rating = 4
        before = {d.id: d.text for d in db.scalars(select(Draft))}
        db.commit()
    aid = collect(client)
    state = client.get('/publications/wb/all?store_id=owner&rating_scope=positive').json()
    assert state['id'] == aid and state['rating_scope'] == 'positive'
    assert state['total'] == 100 and state['excluded'] == 5
    items = client.get(f'/publications/wb/all/{aid}/items?store_id=owner').json()['items']
    assert all(item['rating'] >= 4 for item in items)
    assert {'bulk-0005', 'bulk-0006'} <= {item['review_id'] for item in items}
    assert items[0]['review_text'] == 'Всё в порядке, электролит не протёк. Продавцу отлично.'
    with Session() as db:
        assert before == {d.id: d.text for d in db.scalars(select(Draft))}
    prepared.publish.assert_not_awaited()


def test_positive_list_is_separate_and_does_not_cancel_existing_all_list(client, prepared):
    all_id = collect(client, 'all')
    positive_id = collect(client)
    assert positive_id != all_id
    for scope, aid in [('all', all_id), ('positive', positive_id)]:
        state = client.get(f'/publications/wb/all?store_id=owner&rating_scope={scope}').json()
        assert state['id'] == aid and state['status'] == 'pending'
    repeated = client.post('/publications/wb/all', json={
        'store_id': 'owner', 'include_excluded': True, 'rating_scope': 'positive'})
    assert repeated.json()['id'] == positive_id
    assert client.post('/publications/wb/all', json={'store_id': 'owner', 'rating_scope': 'bad'}).status_code == 422
    assert client.get('/publications/wb/all?store_id=owner&rating_scope=bad').status_code == 422
    prepared.publish.assert_not_awaited()


def test_changed_positive_review_is_not_sent_and_low_rating_stays_for_personal_review(client, prepared):
    aid = collect(client)
    response = client.post(f'/publications/wb/all/{aid}/confirm', json={
        'store_id': 'owner', 'excluded_ack': True, 'rating_scope': 'positive'})
    assert response.status_code == 200, response.text
    with Session() as db:
        jid = db.get(Action, aid).payload['send_job_id']
        db.get(Review, 'bulk-0000').rating = 1
        db.get(Review, 'bulk-0001').text = 'Пришёл сломанный товар'
        db.commit()
    finish(jid)
    with Session() as db:
        assert not db.scalar(select(Publication).where(Publication.review_id.in_(('bulk-0000', 'bulk-0001'))))
        assert db.get(Action, aid).result['published'] == 103


@pytest.mark.parametrize('provider', ['ozon', 'yandex'])
def test_other_marketplaces_use_same_review_rules_and_their_own_destination(client, prepared, provider):
    destinations = []
    async def capture_destination(db, review, text):
        destinations.append(review.marketplace)
    prepared.publish.side_effect = capture_destination
    with Session() as db:
        db.get(StoreProfile, 'owner').provider = provider
        for row in db.scalars(select(Review)):
            row.marketplace = provider
        db.get(Review, 'bulk-0000').rating = 2
        db.get(Review, 'bulk-0001').marketplace = 'wb'  # Wrong provider even in the same store.
        db.get(Review, 'bulk-0002').marketplace = provider + '_question'
        db.commit()
    response = client.post('/publications/reviews/all', json={
        'store_id':'owner', 'rating_scope':'positive', 'include_excluded':True})
    assert response.status_code == 200, response.text
    aid = response.json()['id']
    with Session() as db:
        jid = db.get(Action, aid).payload['prepare_job_id']
    bulk.prepare_step(jid)
    state = client.get('/publications/reviews/all?store_id=owner&rating_scope=positive').json()
    assert state['provider'] == provider and state['total'] == 102
    assert state['excluded'] == 1
    assert client.get(f'/publications/reviews/all/{aid}/items?store_id=owner').status_code == 200
    prepared.publish.assert_not_awaited()
    response = client.post(f'/publications/reviews/all/{aid}/confirm', json={
        'store_id':'owner', 'excluded_ack':True, 'rating_scope':'positive'})
    assert response.status_code == 200, response.text
    with Session() as db:
        jid = db.get(Action, aid).payload['send_job_id']
    finish(jid)
    assert prepared.publish.await_count == 102
    assert destinations == [provider] * 102
    with Session() as db:
        assert not db.scalar(select(Publication).where(Publication.review_id.in_(('bulk-0000','bulk-0001','bulk-0002'))))


def test_yandex_never_silently_truncates_a_saved_reply(client, prepared):
    with Session() as db:
        db.get(StoreProfile, 'owner').provider = 'yandex'
        for row in db.scalars(select(Review)):
            row.marketplace = 'yandex'
        draft = db.get(Draft, 'draft-bulk-0000')
        draft.text = 'Добрый день! ' + 'Спасибо за оценку. ' * 230
        original = draft.text
        assert 4096 < len(original) < 5000
        db.commit()
    aid = collect(client)
    state = client.get('/publications/reviews/all?store_id=owner&rating_scope=positive').json()
    assert state['id'] == aid and state['total'] == 104 and state['excluded'] == 1
    with Session() as db:
        assert db.get(Draft, 'draft-bulk-0000').text == original
    prepared.publish.assert_not_awaited()

