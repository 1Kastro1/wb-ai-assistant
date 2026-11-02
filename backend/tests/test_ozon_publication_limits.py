import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select, func
from app import integrations, services, config, main, scheduled_publication as automatic, bulk_publication as bulk
from app.db import Session, Review, Draft, Product, StoreProfile, Account, Publication, Action, Job, set_setting
from app.publication_constraints import publication_restriction


@pytest.mark.parametrize('market,text,blocked', [('ozon','',True),('ozon',chr(10)+chr(9),True),('ozon','👍',False),('ozon','Отлично!',False),('wb','',False),('yandex','',False),('ozon_question','',False)])
def test_only_ozon_empty_ratings_are_restricted(market,text,blocked):
    assert bool(publication_restriction(SimpleNamespace(marketplace=market,text=text))) == blocked


@pytest.fixture
def prepared(client,monkeypatch):
    monkeypatch.setattr(config,'REAL_PUBLISH',True)
    monkeypatch.setattr(services,'REAL_PUBLISH',True)
    monkeypatch.setattr(main,'run_job_queue',AsyncMock())
    provider=SimpleNamespace(publish=AsyncMock())
    monkeypatch.setattr(services,'provider_for',lambda store:provider)
    with Session() as db:
        set_setting(db,'test_mode',False);set_setting(db,'safe_mode',False)
        db.add(StoreProfile(id='oz-limit',provider='ozon',name='Ozon'))
        db.add(Account(id='oz-limit',encrypted_token='fake'))
        db.add(Product(id='oz-product',name='Товар',brand='TEST'));db.flush()
        for rid,text in [('a-empty',''),('b-text','Всё отлично!')]:
            db.add(Review(id=rid,wb_review_id=rid,wb_account_id='oz-limit',product_id='oz-product',
                          marketplace='ozon',rating=5,text=text,status='draft_ready',created_at='2026-01-01T00:00:00+00:00'))
            db.flush()
            answer='Добрый день! Спасибо за Ваш отзыв!'
            db.add(Draft(id=rid,review_id=rid,text=answer,original=answer,revision=7,quality={'passed':True}))
        db.commit()
    return provider


def test_empty_rating_cannot_create_or_confirm_a_publication(client,prepared):
    with Session() as db:
        with pytest.raises(ValueError,match='Ozon не принимает'):
            services.propose_publish(db,['a-empty'])
        db.rollback()
        db.add(Action(id='old',kind='publish',payload={'test_mode':False,'items':[{
            'review_id':'a-empty','draft_id':'a-empty','revision':7,'text':db.get(Draft,'a-empty').text}]}));db.commit()
        with pytest.raises(ValueError,match='Ozon не принимает'):
            asyncio.run(services.confirm_action(db,'old'))
        db.rollback()
        assert db.scalar(select(func.count()).select_from(Publication))==0
        assert db.get(Draft,'a-empty').revision==7
    prepared.publish.assert_not_awaited()


def test_mixed_manual_selection_preserves_text_and_skips_empty(client,prepared):
    with Session() as db:
        action=services.propose_publish(db,['a-empty','b-text'])
        assert action['payload']['excluded']==['a-empty']
        assert [i['review_id'] for i in action['payload']['items']]==['b-text']
        assert db.get(Review,'a-empty').status=='draft_ready'
        assert db.get(Draft,'a-empty').revision==7


def test_bulk_owner_override_cannot_include_empty_rating(client,prepared):
    with Session() as db:
        review,draft=db.get(Review,'a-empty'),db.get(Draft,'a-empty')
        assert bulk.exclusion_reason(db,review,draft,[])=='not_ready'
        assert bulk.owner_review_reason(db,review,draft,[])=='not_ready'


def test_scheduled_empty_rating_does_not_stop_textual_review(client,prepared):
    # Newest first means the empty rating is visited before the text review.
    with Session() as db:
        db.get(Review,'a-empty').created_at='2026-02-01T00:00:00+00:00';db.commit()
    result=client.patch('/settings/schedule',json={'publication_enabled':True,'publication_questions':False})
    assert result.status_code==200,result.text
    with Session() as db:
        jid=db.scalar(select(Job.id).where(Job.kind==automatic.KIND,Job.result['account_id'].as_string()=='oz-limit'))
    asyncio.run(automatic.run_step(jid))
    with Session() as db:
        job=db.get(Job,jid)
        assert job.status=='completed'
        assert job.result['published']==1 and job.result['skipped']==1 and job.result['uncertain']==0
        assert db.scalar(select(func.count()).select_from(Publication).where(Publication.review_id=='a-empty'))==0
        assert db.get(Draft,'a-empty').revision==7
        assert db.get(Review,'b-text').is_answered
    assert prepared.publish.await_count==1


@pytest.mark.parametrize('response,accepted',[({'comment_id':'accepted-id'},True),({},False),({'comment_id':''},False),({'comment_id':False},False)])
def test_ozon_requires_explicit_creation_acknowledgement(client,monkeypatch,response,accepted):
    request=AsyncMock(return_value=response)
    monkeypatch.setattr(integrations.guard,'request',request)
    monkeypatch.setattr(integrations,'store_credentials',lambda db,sid:(SimpleNamespace(config={'client_id':'123'}),'fake'))
    review=SimpleNamespace(wb_account_id='oz-limit',marketplace='ozon',wb_review_id='remote',text='Отлично!')
    if accepted:
        asyncio.run(integrations.ozon.publish(None,review,'Спасибо!'))
    else:
        with pytest.raises(ValueError,match='не подтвердил'):
            asyncio.run(integrations.ozon.publish(None,review,'Спасибо!'))
    assert request.await_count==1  # A missing acknowledgement must never replay a write.


def test_provider_itself_does_not_send_empty_rating(client,monkeypatch):
    request=AsyncMock();monkeypatch.setattr(integrations.guard,'request',request)
    monkeypatch.setattr(integrations,'store_credentials',lambda db,sid:(SimpleNamespace(config={'client_id':'123'}),'fake'))
    review=SimpleNamespace(wb_account_id='oz-limit',marketplace='ozon',wb_review_id='remote',text='')
    with pytest.raises(ValueError,match='Ozon не принимает'):
        asyncio.run(integrations.ozon.publish(None,review,'Спасибо!'))
    request.assert_not_awaited()
