import asyncio
import pytest
from app.assistant import registry
from app.db import Session, Action
from sqlalchemy import select


def test_runtime_exec_only_proposes(client,seeded):
    client.post('/reviews/r3/draft',json={})
    with Session() as db:
        result=asyncio.run(registry.dispatch(db,'reviews.publish',{}, {'last_result_ids':['r3']}))
        assert result['status']=='pending'


def test_runtime_memory_save_requires_owner_confirmation():
    with Session() as db:
        result=asyncio.run(registry.dispatch(db,'memory.save',{'text':'Пишите кратко'},{}))
        assert result['kind']=='memory' and result['status']=='pending'


def test_runtime_cannot_invent_vehicle():
    with Session() as db:
        result=asyncio.run(registry.dispatch(db,'compatibility.check',{'vehicle':{'verified':True}},{}))
        assert result['status']=='INSUFFICIENT_DATA'


def test_combined_search_and_drafts(client,seeded):
    response=client.post('/chat',json={'text':'Найди негативные отзывы без ответа за неделю и подготовь ответы'}).json()
    assert len(response['data'])==2
    assert set(response['context']['drafted_ids'])=={'r1','r2'}


def test_structured_tool_route(client,seeded,monkeypatch):
    from app.integrations import ollama
    async def planner(*args,**kwargs):
        return {'tool':'reviews.search','arguments':{'max_rating':3},'answer':''}
    monkeypatch.setattr(ollama,'structured',planner)
    response=client.post('/chat',json={'text':'Что покупателям не понравилось?'}).json()
    assert len(response['data'])==2


def test_unknown_planned_tool_rejected(client,monkeypatch):
    from app.integrations import ollama
    async def planner(*args,**kwargs):return {'tool':'shell','arguments':{},'answer':''}
    monkeypatch.setattr(ollama,'structured',planner)
    response=client.post('/chat',json={'text':'Запусти shell'}).json()
    assert response['data'] is None and 'инструмент' in response['text']


def test_job_sync(client,monkeypatch):
    from app.integrations import wb
    client.post('/settings/wb-token',json={'token':'test-job-token-123'})
    async def sync(db):return 12
    monkeypatch.setattr(wb,'sync_products',sync)
    job=client.post('/jobs/sync/products').json()
    result=client.get('/jobs/'+job['id']).json()
    assert result['status']=='completed' and result['result']['processed']==12


def test_chat_uses_known_vin_and_selected_product(client,seeded):
    import json
    from app.compatibility import import_catalog
    vehicle={'make':'Test','model':'Car','generation':'G','engine_code':'E','year':2018,'axle':'front','verified':True,'source':'fixture'}
    client.post('/vin',json={'vin':'JTMAB3FV10D123456','profile':vehicle})
    with Session() as db:
        import_catalog(db,json.dumps([{'brand':'Brand A','part_number':'TEST','wb_nm_id':'p1','category':'brake_pads','make':'Test','model':'Car','generation':'G','engine_code':'E','year_from':2017,'year_to':2020,'axle':'front'}]).encode(),'test.json','fixture',True)
    response=client.post('/chat',json={'text':'Есть колодки той же фирмы? VIN JTMAB3FV10D123456','selection':{'selected_product_id':'p1'}}).json()
    assert 'VERIFIED_FIT' in response['text']
    assert 'JTMAB3FV10D123456' not in response['text']
    assert response['context']['selected_vehicle_id']
