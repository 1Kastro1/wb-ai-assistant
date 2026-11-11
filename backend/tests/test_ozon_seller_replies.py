import asyncio
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import pytest
from app.db import Session, Review, Product, StoreProfile, Draft, Publication
from app import ozon_reviews as sync
from app import integrations


@pytest.mark.parametrize('payload',[{'comments':[]},{'comments':[{'is_owner':False,'text':'buyer'}]},
    {'comments':[{'is_official':True,'text':'Ozon'}]}, {'comments':[{'is_owner':'true','text':'not boolean'}]},
    {'comments':[{'is_owner':True,'parent_comment_id':'buyer','text':'thread'}]}])
def test_comments_are_not_a_seller_reply(monkeypatch,payload):
    monkeypatch.setattr(sync.guard,'request',AsyncMock(return_value=payload))
    assert asyncio.run(sync.seller_reply({},'r'))=={'state':'absent','text':''}


@pytest.mark.parametrize('status,state',[('', 'published'),('PUBLISHED','published'),('ON_MODERATION','answer_pending'),('REJECTED','answer_rejected')])
def test_owned_comment_text_and_status(monkeypatch,status,state):
    request=AsyncMock(return_value={'comments':[{'id':'c','is_owner':True,'status':status,'text':'Ответ продавца'}]})
    monkeypatch.setattr(sync.guard,'request',request)
    assert asyncio.run(sync.seller_reply({},'r'))=={'state':state,'text':'Ответ продавца'}
    assert request.call_args.args==('ozon','review_comments')


def test_seller_comment_can_be_on_later_page(monkeypatch):
    request=AsyncMock(side_effect=[{'comments':[{'is_owner':False}]*100,'offset':100},
        {'comments':[{'is_owner':True,'text':'Ответ'}]}])
    monkeypatch.setattr(sync.guard,'request',request)
    assert asyncio.run(sync.seller_reply({},'r'))['text']=='Ответ'
    assert request.call_args.kwargs['body']['offset']==100


@pytest.mark.parametrize('published,rejected,state',[(True,False,'published'),(False,True,'answer_rejected'),(False,False,'answer_pending')])
def test_live_ozon_moderation_flags(monkeypatch,published,rejected,state):
    monkeypatch.setattr(sync.guard,'request',AsyncMock(return_value={'comments':[{
        'is_owner':True,'text':'Seller reply','is_published':published,'is_rejected':rejected}],'offset':1}))
    assert asyncio.run(sync.seller_reply({},'r'))=={'state':state,'text':'Seller reply'}


@pytest.mark.parametrize('payload',[{}, {'comments':[{'is_owner':True,'status':'NEW_UNDOCUMENTED','text':'Ответ'}]},
    {'comments':[{'is_owner':True,'text':'Ответ','review_id':'other'}]},
    {'comments':[{'is_owner':True,'text':''}]}])
def test_bad_or_unrecognised_response_cannot_clear_saved_state(monkeypatch,payload):
    monkeypatch.setattr(sync.guard,'request',AsyncMock(return_value=payload))
    with pytest.raises(ValueError):asyncio.run(sync.seller_reply({},'r'))


def seed():
    with Session() as db:
        db.add(StoreProfile(id='ozon-shop',provider='ozon',name='Ozon',config={'client_id':'1'}))
        db.add(Product(id='ozon:ozon-shop:123',name='Товар',marketplace='ozon'));db.flush()
        db.add(Review(id='r',wb_account_id='ozon-shop',wb_review_id='remote',product_id='ozon:ozon-shop:123',
            rating=5,text='Понравилось',marketplace='ozon',is_answered=True,status='published',existing_answer=''))
        db.flush();db.add(Draft(id='d',review_id='r',text='Ручной черновик',original='Старый',revision=8,edits=[{'by':'employee'}]));db.commit()


@pytest.mark.parametrize('owned',[True,False])
def test_resync_corrects_old_flag_and_preserves_draft(client,monkeypatch,owned):
    seed()
    async def request(provider,operation,**kwargs):
        if operation=='reviews':return {'reviews':[{'id':'remote','sku':123,'rating':5,'text':'Понравилось','comments_amount':1}], 'has_next':False}
        assert operation=='review_comments'
        return {'comments':[{'is_owner':owned,'text':'Подтверждённый ответ'}]}
    monkeypatch.setattr(sync.guard,'request',request)
    monkeypatch.setattr(integrations,'store_credentials',lambda db,store:(db.get(StoreProfile,store),'token'))
    with Session() as db:
        assert asyncio.run(sync.sync_reviews(integrations.OzonProvider(),db,'ozon-shop'))==1
        row=db.get(Review,'r');db.refresh(row)
        assert row.is_answered is owned
        assert row.existing_answer==('Подтверждённый ответ' if owned else '')
        draft=db.get(Draft,'d')
        assert draft.text=='Ручной черновик' and draft.revision==8 and draft.edits==[{'by':'employee'}]


def test_opaque_cursor_checkpoint_preserves_progress(client,monkeypatch):
    seed(); checkpoints=[]
    async def request(provider,operation,**kwargs):
        if operation=='review_comments': return {'comments':[]}
        return {'reviews':[{'id':'remote','sku':123,'rating':5,'text':'Saved review'}], 'last_id':'opaque-cursor','has_next':True}
    monkeypatch.setattr(sync.guard,'request',request)
    monkeypatch.setattr(integrations,'store_credentials',lambda db,store:(db.get(StoreProfile,store),'token'))
    with Session() as db:
        with pytest.raises(ValueError,match='повторил'):
            asyncio.run(sync.sync_reviews(integrations.OzonProvider(),db,'ozon-shop',checkpoint=checkpoints.append))
        assert checkpoints==[
            {'last_id':'','processed':1,'page_done_ids':['remote']},
            {'last_id':'opaque-cursor','processed':1}]
        assert db.get(Review,'r').text=='Saved review'


def test_timeout_preserves_known_published_reply(client,monkeypatch):
    seed()
    with Session() as db:
        row=db.get(Review,'r');row.existing_answer='Уже опубликовано';db.commit()
    async def request(provider,operation,**kwargs):
        if operation=='reviews':return {'reviews':[{'id':'remote','sku':123,'rating':5,'comments_amount':1}],'has_next':False}
        raise ValueError('HTTP 429')
    monkeypatch.setattr(sync.guard,'request',request)
    monkeypatch.setattr(integrations,'store_credentials',lambda db,store:(db.get(StoreProfile,store),'token'))
    with Session() as db:
        with pytest.raises(ValueError):asyncio.run(sync.sync_reviews(integrations.OzonProvider(),db,'ozon-shop'))
    with Session() as db:
        assert db.get(Review,'r').existing_answer=='Уже опубликовано' and db.get(Review,'r').is_answered


def test_concurrent_publication_wins_and_remote_absence_never_reopens_confirmed_send(client):
    seed()
    with Session() as db:
        row=db.get(Review,'r');expected=sync.snapshot(row)
        row.existing_answer='Новый опубликованный ответ';db.commit()
        assert sync.apply_reply(db,'r',expected,{'state':'absent','text':''})=='changed'
        row=db.get(Review,'r');expected=sync.snapshot(row)
        db.add(Publication(id='pub',review_id='r',action_id='a',status='published'));db.commit()
        assert sync.apply_reply(db,'r',expected,{'state':'absent','text':''})=='answer_unknown'
        assert db.get(Review,'r').existing_answer=='Новый опубликованный ответ'
        assert db.get(Review,'r').is_answered
