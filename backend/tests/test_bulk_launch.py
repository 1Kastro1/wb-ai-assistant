from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
import pytest
from app import bulk_publication as bulk
from app.db import Session, Action, Job, Review, Draft, User, set_setting
from test_bulk_publication import prepared, finish


def launch(client, scope='positive'):
    response = client.post('/publications/reviews/all/launch', json={'store_id':'owner','rating_scope':scope})
    assert response.status_code == 200, response.text
    return response.json()


def prepare_job(aid):
    with Session() as db:
        return db.get(Action, aid).payload['prepare_job_id']


def send_job(aid):
    with Session() as db:
        return db.get(Action, aid).payload['send_job_id']


def test_one_explicit_click_collects_then_sends_without_browser_or_second_confirmation(client, prepared):
    with Session() as db:
        db.get(Review,'bulk-0000').rating=2
        before={d.id:d.text for d in db.scalars(select(Draft))}
        db.commit()
    state=launch(client)
    assert state['status']=='preparing' and state['auto_send']
    assert launch(client)['id']==state['id']
    prepared.publish.assert_not_awaited()
    bulk.prepare_step(prepare_job(state['id']))
    with Session() as db:
        parent=db.get(Action,state['id'])
        assert parent.status=='sending' and parent.result['confirmed_by']=='owner'
        assert parent.result['excluded_ack']
    finish(send_job(state['id']))
    assert prepared.publish.await_count==104
    with Session() as db:
        assert before=={d.id:d.text for d in db.scalars(select(Draft))}


def test_launch_reuses_existing_preparation_without_resetting_progress(client,prepared):
    old=client.post('/publications/reviews/all',json={'store_id':'owner','include_excluded':True,'rating_scope':'all'}).json()
    state=launch(client,'all')
    assert state['id']==old['id'] and state['auto_send']
    bulk.prepare_step(prepare_job(state['id']))
    assert client.get('/publications/reviews/all?store_id=owner').json()['status']=='sending'
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==bulk.PREPARE_KIND))==1
    prepared.publish.assert_not_awaited()


@pytest.mark.parametrize('change',['role','safe_mode','test_mode'])
def test_changed_authority_or_mode_does_not_start_automatic_publication(client,prepared,change):
    state=launch(client)
    with Session() as db:
        if change=='role':db.get(User,'owner').role='employee'
        else:set_setting(db,change,True)
        db.commit()
    bulk.prepare_step(prepare_job(state['id']))
    with Session() as db:
        parent=db.get(Action,state['id'])
        assert parent.status=='pending' and not parent.payload['auto_send']
        assert parent.payload['auto_send_error']
        assert not db.scalar(select(Job.id).where(Job.kind==bulk.SEND_KIND))
    prepared.publish.assert_not_awaited()


def test_launch_cancellation_never_turns_into_publication(client,prepared):
    state=launch(client)
    response=client.post('/publications/reviews/all/'+state['id']+'/cancel',json={'store_id':'owner'})
    assert response.status_code==200
    bulk.prepare_step(prepare_job(state['id']))
    with Session() as db:
        assert db.get(Action,state['id']).status=='cancelled'
        assert not db.scalar(select(Job.id).where(Job.kind==bulk.SEND_KIND))
    prepared.publish.assert_not_awaited()


def test_crash_after_collection_resumes_authorized_send_once(client,prepared,monkeypatch):
    state=launch(client)
    original=bulk.auto_confirm
    def crash(*args):raise RuntimeError('stopped between durable transitions')
    monkeypatch.setattr(bulk,'auto_confirm',crash)
    jid=prepare_job(state['id'])
    with pytest.raises(RuntimeError):bulk.prepare_step(jid)
    with Session() as db:
        assert db.get(Job,jid).status=='queued'
        assert db.get(Action,state['id']).status=='pending'
    monkeypatch.setattr(bulk,'auto_confirm',original)
    bulk.prepare_step(jid)
    assert launch(client)['status']=='sending'
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==bulk.SEND_KIND))==1
    finish(send_job(state['id']))
    assert prepared.publish.await_count==105


def test_expired_list_is_preserved_and_replaced_by_fresh_selection(client,prepared):
    old=client.post('/publications/reviews/all',json={'store_id':'owner','include_excluded':True,'rating_scope':'positive'}).json()
    bulk.prepare_step(prepare_job(old['id']))
    with Session() as db:
        parent=db.get(Action,old['id'])
        parent.payload={**parent.payload,'prepared_at':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()}
        db.commit()
    new=launch(client)
    assert new['id']!=old['id'] and new['status']=='preparing'
    with Session() as db:
        assert db.get(Action,old['id']).status=='expired'
        assert db.scalar(select(func.count()).select_from(Draft))==105
    prepared.publish.assert_not_awaited()

