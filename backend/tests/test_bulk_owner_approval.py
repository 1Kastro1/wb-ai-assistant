import asyncio
import pytest
from sqlalchemy import select
from app import bulk_publication as bulk, services
from app.db import Session, Action, Review, Draft, Job, Memory, User, Publication
from test_bulk_publication import prepared, prepare, finish


def owner_package(client):
    result = client.post('/publications/wb/all', json={'store_id':'owner','include_excluded':True})
    assert result.status_code == 200, result.text
    aid = result.json()['id']
    with Session() as db:
        jid = db.get(Action, aid).payload['prepare_job_id']
    bulk.prepare_step(jid)
    return aid


def test_owner_can_include_stale_unverified_and_low_rating_without_rewriting(client, prepared):
    with Session() as db:
        review=db.get(Review,'bulk-0000'); review.rating=2; review.manual=True; review.status='manual_review'
        draft=db.get(Draft,'draft-bulk-0000'); draft.quality={'passed':False,'owner_rules':{'technical_error':True,'passed':False,'checked':False}}
        original=draft.text
        db.commit()
    aid=owner_package(client)
    state=client.get('/publications/wb/all?store_id=owner').json()
    assert state['include_excluded'] and state['total']==105 and state['included_excluded']==1
    assert client.post(f'/publications/wb/all/{aid}/confirm',json={'store_id':'owner'}).status_code==400
    prepared.publish.assert_not_awaited()
    response=client.post(f'/publications/wb/all/{aid}/confirm',json={'store_id':'owner','excluded_ack':True})
    assert response.status_code==200, response.text
    with Session() as db: jid=db.get(Action,aid).payload['send_job_id']
    finish(jid)
    assert prepared.publish.await_count==105
    with Session() as db:
        draft=db.get(Draft,'draft-bulk-0000')
        assert draft.text==original and draft.quality['owner_rules']['technical_error']
        assert db.get(Action,aid).result['excluded_ack'] is True


@pytest.mark.parametrize('changed',['text','rules','buyer','role'])
def test_owner_approval_is_invalidated_by_changes(client,prepared,changed):
    aid=owner_package(client)
    response=client.post(f'/publications/wb/all/{aid}/confirm',json={'store_id':'owner','excluded_ack':True})
    assert response.status_code==200
    with Session() as db:
        parent=db.get(Action,aid); jid=parent.payload['send_job_id']
        actor=db.get(User,parent.result['confirmed_by'])
        if changed=='text': db.get(Draft,'draft-bulk-0000').text='Другой ответ'
        if changed=='buyer': db.get(Review,'bulk-0000').text='Теперь покупатель описал другую ситуацию'
        if changed=='rules': db.add(Memory(id='new-owner-rule',scope='global',text='Всегда добавляй подпись'))
        if changed=='role': actor.role='employee'
        db.commit()
    finish(jid)
    with Session() as db:
        assert not db.scalar(select(Publication).where(Publication.review_id=='bulk-0000'))


def test_exceptions_never_include_known_content_errors_or_previous_attempts(client,prepared):
    with Session() as db:
        db.get(Draft,'draft-bulk-0000').text=''
        db.add(Publication(id='already',review_id='bulk-0001',action_id='old',status='uncertain'))
        db.commit()
    aid=owner_package(client)
    state=client.get('/publications/wb/all?store_id=owner').json()
    assert state['total']==103 and state['excluded']==2


def test_generic_publish_cannot_forge_owner_approval(client,prepared):
    with Session() as db:
        db.add(Memory(id='signature-rule',scope='global',text='В конце каждого ответа добавляй подпись «С уважением ГК Юником.»'))
        draft=db.get(Draft,'draft-bulk-0000'); draft.quality={'passed':False,'owner_rules':{'checked':False}}
        db.add(Action(id='forged',kind='publish',payload={'test_mode':False,'bulk_parent':'forged-parent','items':[
            {'review_id':draft.review_id,'draft_id':draft.id,'revision':draft.revision,'text':draft.text,'kind':'review','manual':False,'rating':5,'owner_review':True}]}))
        db.commit()
        with pytest.raises(ValueError): asyncio.run(services.confirm_action(db,'forged',True,'owner'))
    prepared.publish.assert_not_awaited()
