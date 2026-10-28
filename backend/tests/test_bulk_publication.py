import asyncio
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select, func
from app import main, services, config, bulk_publication as bulk
from app.db import Session, Action, Job, Review, Draft, Product, Publication, StoreProfile, Memory, User, set_setting


@pytest.fixture
def prepared(client, monkeypatch):
    monkeypatch.setattr(main, 'REAL_PUBLISH', True)
    monkeypatch.setattr(services, 'REAL_PUBLISH', True)
    monkeypatch.setattr(config, 'REAL_PUBLISH', True)
    monkeypatch.setattr(bulk, 'publication_pause', AsyncMock())
    provider = type('Provider', (), {'publish': AsyncMock()})()
    monkeypatch.setattr(services, 'provider_for', lambda store: provider)
    with Session() as db:
        set_setting(db, 'test_mode', False); set_setting(db, 'safe_mode', False)
        db.add(Product(id='bulk-product', name='Автотовар', brand='TEST'))
        db.flush()
        for i in range(105):
            rid = f'bulk-{i:04}'
            db.add(Review(id=rid, wb_review_id=rid, wb_account_id='owner', product_id='bulk-product',
                          marketplace='wb', rating=5, text='', status='draft_ready'))
        db.flush()
        for i in range(105):
            rid = f'bulk-{i:04}'
            db.add(Draft(id='draft-'+rid, review_id=rid, text='Добрый день! Спасибо за высокую оценку!',
                         original='Добрый день! Спасибо за высокую оценку!', quality={'passed': True}))
        db.commit()
    return provider


def prepare(client):
    response = client.post('/publications/wb/all', json={'store_id': 'owner'})
    assert response.status_code == 200, response.text
    aid = response.json()['id']
    with Session() as db:
        jid = db.get(Action, aid).payload['prepare_job_id']
    bulk.prepare_step(jid)
    return aid


def confirm(client, aid):
    response = client.post(f'/publications/wb/all/{aid}/confirm', json={'store_id': 'owner'})
    assert response.status_code == 200, response.text
    with Session() as db:
        return db.get(Action, aid).payload['send_job_id']


def finish(jid):
    for _ in range(40):
        with Session() as db:
            if db.get(Job, jid).status == 'completed':
                return
        asyncio.run(bulk.send_step(jid))
    raise AssertionError('Sender did not finish bounded slices')


def test_prepare_spans_all_pages_and_never_publishes_without_confirmation(client, prepared):
    aid = prepare(client)
    status = client.get('/publications/wb/all?store_id=owner').json()
    assert status['status'] == 'pending'
    assert status['total'] == status['scanned'] == 105
    assert status['published'] == 0
    prepared.publish.assert_not_awaited()
    page = client.get(f'/publications/wb/all/{aid}/items?store_id=owner&offset=100').json()
    assert len(page['items']) == 5 and page['total'] == 105
    repeated = client.post('/publications/wb/all', json={'store_id': 'owner'}).json()
    assert repeated['id'] == aid
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Publication)) == 0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == bulk.SEND_KIND)) == 0
    assert client.get('/actions').json() == []
    assert client.post(f'/actions/{aid}/confirm', json={'manual_ack': True}).status_code == 400


def test_manifest_excludes_unverified_manual_published_and_foreign_rows(client, prepared):
    with Session() as db:
        db.add(StoreProfile(id='other', provider='ozon', name='Other'))
        db.get(Review, 'bulk-0000').wb_account_id = 'other'
        db.get(Review, 'bulk-0001').marketplace = 'wb_question'
        db.get(Review, 'bulk-0002').rating = 2
        db.get(Review, 'bulk-0003').manual = True
        db.get(Review, 'bulk-0004').risk = 'HIGH'
        db.get(Review, 'bulk-0005').is_answered = True
        db.get(Review, 'bulk-0006').status = 'pending_confirmation'
        db.get(Draft, 'draft-bulk-0007').quality = {'passed': False}
        db.get(Draft, 'draft-bulk-0008').quality = {'passed': True, 'owner_rules': {'checked': False, 'passed': False}}
        db.add(Publication(id='old-slot', review_id='bulk-0009', action_id='old', status='uncertain'))
        db.commit()
    aid = prepare(client)
    with Session() as db:
        parent = db.get(Action, aid)
        assert parent.payload['total'] == 95
        assert parent.payload['scanned'] == 102
        assert parent.payload['excluded'] == 7
    prepared.publish.assert_not_awaited()


@pytest.mark.parametrize('blocked', ['test_mode', 'safe_mode', 'launch'])
def test_modes_block_real_bulk_confirmation(client, prepared, monkeypatch, blocked):
    aid = prepare(client)
    if blocked == 'launch':
        monkeypatch.setattr(main, 'REAL_PUBLISH', False)
    else:
        with Session() as db:
            set_setting(db, blocked, True); db.commit()
    response = client.post(f'/publications/wb/all/{aid}/confirm', json={'store_id': 'owner'})
    assert response.status_code == 400 and 'заблокирована' in response.json()['detail']
    prepared.publish.assert_not_awaited()


def test_confirm_schedules_one_durable_job_and_sends_all_105_once(client, prepared):
    aid = prepare(client); jid = confirm(client, aid)
    assert confirm(client, aid) == jid
    prepared.publish.assert_not_awaited()
    finish(jid)
    with Session() as db:
        parent = db.get(Action, aid)
        assert parent.status == 'completed'
        assert parent.result['published'] == parent.result['processed'] == 105
        assert db.scalar(select(func.count()).select_from(Publication)) == 105
        assert db.scalar(select(func.count()).select_from(Review).where(Review.is_answered == True)) == 105
    assert prepared.publish.await_count == 105
    asyncio.run(bulk.send_step(jid))
    assert prepared.publish.await_count == 105


def test_changed_text_and_rules_are_rechecked_before_any_network_write(client, prepared):
    aid = prepare(client); jid = confirm(client, aid)
    with Session() as db:
        draft = db.get(Draft, 'draft-bulk-0000')
        draft.text, draft.revision = 'Другой текст ответа.', 2
        db.add(Memory(id='new-rule', kind='REVIEW_RULE', text='Обязательно сообщи код упаковки.'))
        db.commit()
    finish(jid)
    prepared.publish.assert_not_awaited()
    with Session() as db:
        assert db.get(Action, aid).result['skipped'] == 105
        assert db.scalar(select(func.count()).select_from(Publication)) == 0


def test_completed_child_is_not_republished_after_checkpoint_crash(client, prepared, monkeypatch):
    aid = prepare(client); jid = confirm(client, aid)
    original = services.confirm_action
    async def crash_after_commit(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError('worker terminated before manifest checkpoint')
    monkeypatch.setattr(services, 'confirm_action', crash_after_commit)
    with pytest.raises(RuntimeError):
        asyncio.run(bulk.send_step(jid))
    assert prepared.publish.await_count == 1
    monkeypatch.setattr(services, 'confirm_action', original)
    finish(jid)
    assert prepared.publish.await_count == 105
    with Session() as db:
        assert db.get(Action, aid).result['published'] == 105


def test_uncertain_request_is_never_automatically_retried(client, prepared):
    prepared.publish.side_effect = ValueError('response lost after request')
    aid = prepare(client); jid = confirm(client, aid)
    finish(jid)
    assert prepared.publish.await_count == 105
    with Session() as db:
        assert db.get(Action, aid).result['uncertain'] == 105
        assert db.get(Action, aid).status == 'needs_review'
    second = prepare(client)
    assert client.get('/publications/wb/all?store_id=owner').json()['total'] == 0
    assert second != aid
    assert prepared.publish.await_count == 105


def test_store_switch_permissions_and_cancelled_manifest_block_sending(client, prepared):
    aid = prepare(client)
    assert client.post('/publications/wb/all', json={'store_id': 'foreign'}).status_code == 409
    main.app.dependency_overrides[main.authorize] = lambda: {'role': 'member', 'user_id': 'member', 'permissions': [], 'store_ids': []}
    try:
        assert client.get('/publications/wb/all?store_id=owner').status_code == 403
    finally:
        main.app.dependency_overrides.clear()
    response = client.post(f'/publications/wb/all/{aid}/cancel', json={'store_id': 'owner'})
    assert response.status_code == 200
    assert client.post(f'/publications/wb/all/{aid}/confirm', json={'store_id': 'owner'}).status_code == 400
    prepared.publish.assert_not_awaited()


def test_mode_change_after_confirmation_stops_remaining_writes(client, prepared):
    aid = prepare(client); jid = confirm(client, aid)
    with Session() as db:
        set_setting(db, 'safe_mode', True); db.commit()
    finish(jid)
    prepared.publish.assert_not_awaited()
    with Session() as db:
        assert db.get(Action, aid).status == 'needs_review'


def test_changed_rating_is_excluded_even_if_old_draft_verdict_passed(client, prepared):
    aid = prepare(client); jid = confirm(client, aid)
    with Session() as db:
        db.get(Review, 'bulk-0000').rating = 1
        db.commit()
    finish(jid)
    assert prepared.publish.await_count == 104
    with Session() as db:
        assert db.get(Action, aid).result['skipped'] == 1
        assert not db.get(Review, 'bulk-0000').is_answered


@pytest.mark.parametrize('change', ['provider', 'permission'])
def test_revoked_access_or_provider_change_stops_publication(client, prepared, change):
    aid = prepare(client); jid = confirm(client, aid)
    with Session() as db:
        if change == 'provider':
            db.get(StoreProfile, 'owner').provider = 'ozon'
        else:
            actor = db.get(User, 'owner')
            actor.role, actor.permissions = 'member', ['data:read']
        db.commit()
    finish(jid)
    prepared.publish.assert_not_awaited()


def test_cancellation_during_request_finishes_only_that_request(client, prepared):
    aid = prepare(client); jid = confirm(client, aid)
    async def stop_after_first_request(*args):
        with Session() as db:
            bulk.cancel(db, db.get(Action, aid))
    prepared.publish.side_effect = stop_after_first_request
    asyncio.run(bulk.send_step(jid))
    assert prepared.publish.await_count == 1
    with Session() as db:
        assert db.get(Action, aid).status == 'cancelled'
        assert db.get(Action, aid).result['published'] == 1
        assert db.get(Job, jid).status == 'cancelled'


@pytest.mark.parametrize('change', ['provider', 'permission'])
def test_access_change_between_requests_stops_next_write(client, prepared, change):
    aid = prepare(client); jid = confirm(client, aid)
    async def revoke_after_first_request(*args):
        with Session() as db:
            if change == 'provider':
                db.get(StoreProfile, 'owner').provider = 'ozon'
            else:
                actor = db.get(User, 'owner')
                actor.role, actor.permissions = 'member', ['data:read']
            db.commit()
    prepared.publish.side_effect = revoke_after_first_request
    asyncio.run(bulk.send_step(jid))
    assert prepared.publish.await_count == 1
    with Session() as db:
        assert db.get(Action, aid).status == 'needs_review'
        assert db.get(Action, aid).result['published'] == 1


def test_uncertain_reserved_child_survives_worker_restart_without_repeat(client, prepared, monkeypatch):
    aid = prepare(client); jid = confirm(client, aid)
    original = services.confirm_action
    async def crash_after_reservation(db, child_id, *args):
        child = db.get(Action, child_id)
        rid = child.payload['items'][0]['review_id']
        child.status = 'executing'
        db.add(Publication(id='reserved-before-crash', review_id=rid, action_id=child_id, status='in_flight'))
        db.get(Review, rid).status = 'publishing'
        db.commit()
        raise RuntimeError('process died after request started')
    monkeypatch.setattr(services, 'confirm_action', crash_after_reservation)
    with pytest.raises(RuntimeError):
        asyncio.run(bulk.send_step(jid))
    with Session() as db:
        main.recover_inflight_publications(db); db.commit()
        assert db.get(Action, aid).status == 'sending'
    monkeypatch.setattr(services, 'confirm_action', original)
    finish(jid)
    assert prepared.publish.await_count == 104
    with Session() as db:
        assert db.get(Action, aid).result['uncertain'] == 1
        assert db.get(Action, aid).result['published'] == 104


def test_preparation_resumes_committed_pages_without_duplicate_items(client, prepared, monkeypatch):
    response = client.post('/publications/wb/all', json={'store_id': 'owner'}).json()
    with Session() as db:
        jid = db.get(Action, response['id']).payload['prepare_job_id']
    original, calls = bulk.eligible, [0]
    def fail_on_second_page(*args):
        calls[0] += 1
        if calls[0] == 101:
            raise RuntimeError('prepare worker stopped after committed page')
        return original(*args)
    monkeypatch.setattr(bulk, 'eligible', fail_on_second_page)
    with pytest.raises(RuntimeError):
        bulk.prepare_step(jid)
    with Session() as db:
        assert db.get(Action, response['id']).payload['total'] == 100
    monkeypatch.setattr(bulk, 'eligible', original)
    asyncio.run(main.run_sync_job(jid))
    with Session() as db:
        parent = db.get(Action, response['id'])
        assert parent.status == 'pending' and parent.payload['total'] == 105
        manifests = list(db.scalars(select(Action).where(Action.kind == bulk.MANIFEST_KIND)))
        ids = [item['review_id'] for manifest in manifests for item in manifest.payload['items']]
        assert len(ids) == len(set(ids)) == 105
    prepared.publish.assert_not_awaited()


def test_foreign_manifest_cannot_be_previewed_or_confirmed(client, prepared):
    aid = prepare(client)
    with Session() as db:
        parent = db.get(Action, aid)
        parent.payload = {**parent.payload, 'account_id': 'another-wb-store'}
        db.commit()
    assert client.get(f'/publications/wb/all/{aid}/items?store_id=owner').status_code == 404
    assert client.post(f'/publications/wb/all/{aid}/confirm', json={'store_id': 'owner'}).status_code == 404
    prepared.publish.assert_not_awaited()


def test_exclusion_filters_reasons_sort_and_pagination(client, prepared):
    with Session() as db:
        for i in range(30):
            db.get(Review, f'bulk-{i:04}').rating = 2 if i < 25 else 3
        db.get(Draft, 'draft-bulk-0030').quality = {'passed': False, 'owner_rules': {
            'checked': False, 'passed': False, 'technical_error': True, 'reason': 'истекло время проверки'}}
        db.get(Review, 'bulk-0031').manual = True
        db.commit()
    aid = prepare(client)
    path = f'/publications/wb/all/{aid}/exclusions?store_id=owner'
    result = client.get(path).json()
    assert result['total'] == result['diagnosed'] == 32
    assert result['counts'] == {'low_rating': 30, 'check_timeout': 1, 'safety': 1}
    assert result['audit_status'] == 'not_needed'
    assert len(result['items']) == 20 and result['has_more']
    result = client.get(path+'&reason=low_rating&offset=20&sort=rating_desc').json()
    assert result['total'] == 30 and len(result['items']) == 10
    assert all(row['exclusion']['reason'] == 'low_rating' for row in result['items'])
    timed_out = client.get(path+'&reason=check_timeout').json()['items']
    assert [item['id'] for item in timed_out] == ['bulk-0030']
    assert timed_out[0]['draft']['text'] and not timed_out[0]['exclusion']['retrospective']
    assert client.get(path+'&q=%25').json()['total'] == 0
    assert client.get(path+'&reason=invalid').status_code == 400
    assert client.get(path+'&offset=-1').status_code == 422
    prepared.publish.assert_not_awaited()


def test_legacy_exclusion_audit_keeps_approved_manifest_and_is_idempotent(client, prepared):
    from sqlalchemy import delete
    from app import bulk_exclusions as exclusions
    with Session() as db:
        db.get(Review, 'bulk-0000').rating = 1
        db.get(Review, 'bulk-0001').rating = 1
        db.commit()
    aid = prepare(client)
    with Session() as db:
        db.execute(delete(Action).where(Action.kind == exclusions.KIND))
        parent = db.get(Action, aid)
        parent.payload = {key: value for key, value in parent.payload.items()
                          if key not in ('exclusion_legacy_cursor', 'exclusion_counts')}
        db.get(Review, 'bulk-0001').rating = 5  # State changed since old preparation.
        db.commit()
    path = f'/publications/wb/all/{aid}/exclusions'
    assert client.get(path+'?store_id=owner').json()['audit_status'] == 'not_started'
    for _ in range(2):
        assert client.post(path+'/audit', json={'store_id': 'owner'}).status_code == 200
    with Session() as db:
        jid = db.get(Action, aid).payload['exclusions_job_id']
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == exclusions.AUDIT_KIND)) == 1
    asyncio.run(main.run_sync_job(jid))
    result = client.get(path+'?store_id=owner').json()
    assert result['audit_status'] == 'completed'
    assert result['counts'] == {'low_rating': 1, 'changed_since_scan': 1}
    assert result['total'] == 2 and all(item['exclusion']['retrospective'] for item in result['items'])
    with Session() as db:
        parent = db.get(Action, aid)
        assert parent.payload['total'] == 103 and parent.payload['excluded'] == 2
        assert parent.status == 'pending'
    prepared.publish.assert_not_awaited()


def test_exclusion_endpoints_enforce_parent_scope(client, prepared):
    aid = prepare(client)
    with Session() as db:
        parent = db.get(Action, aid)
        parent.payload = {**parent.payload, 'account_id': 'other'}
        db.commit()
    path = f'/publications/wb/all/{aid}/exclusions'
    assert client.get(path+'?store_id=owner').status_code == 404
    assert client.get(path+'/summary?store_id=owner').status_code == 404
    assert client.post(path+'/audit', json={'store_id': 'owner'}).status_code == 404


def test_legacy_prefix_and_new_preparation_pages_do_not_duplicate_exclusions(client, prepared, monkeypatch):
    from sqlalchemy import delete
    from app import bulk_exclusions as exclusions
    with Session() as db:
        for review in db.scalars(select(Review)):
            review.rating = 1
        db.commit()
    aid = client.post('/publications/wb/all', json={'store_id': 'owner'}).json()['id']
    with Session() as db:
        jid = db.get(Action, aid).payload['prepare_job_id']
    original, calls = bulk.rating_scope_reason, [0]
    def pause(*args):
        calls[0] += 1
        if calls[0] == 101:
            raise RuntimeError('old worker stopped after first page')
        return original(*args)
    monkeypatch.setattr(bulk, 'rating_scope_reason', pause)
    with pytest.raises(RuntimeError):
        bulk.prepare_step(jid)
    with Session() as db:
        db.execute(delete(Action).where(Action.kind == exclusions.KIND))
        parent = db.get(Action, aid)
        parent.payload = {key: value for key, value in parent.payload.items()
                          if key not in ('exclusion_legacy_cursor', 'exclusion_counts')}
        db.commit()
    path = f'/publications/wb/all/{aid}/exclusions'
    client.post(path+'/audit', json={'store_id': 'owner'})
    monkeypatch.setattr(bulk, 'rating_scope_reason', original)
    bulk.prepare_step(jid)
    with Session() as db:
        audit_id = db.get(Action, aid).payload['exclusions_job_id']
    exclusions.audit_step(audit_id)
    result = client.get(path+'?store_id=owner').json()
    assert result['counts'] == {'low_rating': 105}
    assert result['total'] == result['diagnosed'] == 105
    prepared.publish.assert_not_awaited()


def test_missing_global_verdict_diagnosis_does_not_query_each_product_catalog(client, prepared, monkeypatch):
    from sqlalchemy import delete
    from app import bulk_exclusions as exclusions, owner_reply_rules
    with Session() as db:
        db.add(Memory(id='global-policy', text='Начинай с «Добрый день!».', kind='REVIEW_RULE', scope='global'))
        db.commit()
    aid = prepare(client)
    with Session() as db:
        db.execute(delete(Action).where(Action.kind == exclusions.KIND))
        parent = db.get(Action, aid)
        parent.payload = {key: value for key, value in parent.payload.items()
                          if key not in ('exclusion_legacy_cursor', 'exclusion_counts')}
        db.commit()
    client.post(f'/publications/wb/all/{aid}/exclusions/audit', json={'store_id': 'owner'})
    def unnecessary_lookup(*args, **kwargs):
        raise AssertionError('Missing global verdict needs no catalog lookup')
    monkeypatch.setattr(owner_reply_rules, 'matching_owner_rules', unnecessary_lookup)
    with Session() as db:
        jid = db.get(Action, aid).payload['exclusions_job_id']
    exclusions.audit_step(jid)
    with Session() as db:
        assert db.get(Action, aid).payload['exclusion_counts'] == {'rules_changed': 105}
    prepared.publish.assert_not_awaited()

