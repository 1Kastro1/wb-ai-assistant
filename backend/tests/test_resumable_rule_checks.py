import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.owner_reply_policy import enforce_owner_rules
from app.rule_execution import clear_verdicts

PASS={'compliant':True,'violations':[],'revised_answer':''}

def setup(monkeypatch):
    from app.integrations import ollama
    monkeypatch.setattr('app.owner_reply_policy.rule_groups',lambda rules:[[rule] for rule in rules])
    monkeypatch.setattr('app.model_context.fitting_rule_groups',lambda groups,*args:groups)
    clear_verdicts()
    review=SimpleNamespace(id='resume',text='Хорошо упаковано',rating=4,risk='NORMAL',marketplace='wb')
    product=SimpleNamespace(id='p',name='Товар',brand='B')
    rules=['Учитывай замечание об упаковке.','Пиши по существу отзыва.','Не выдумывай свойства товара.']
    async def run(previous=None, **changes):
        args=dict(reply='Спасибо за отзыв! Рады, что упаковка Вам понравилась.',review=review,product=product,
            rules=rules,facts=[],assess=lambda answer:{'passed':True},check_only=True,previous_check=previous)
        args.update(changes)
        return await enforce_owner_rules(**args)
    return ollama,run

def test_timeout_saves_progress_and_next_check_finishes_remaining_groups(monkeypatch):
    ollama,run=setup(monkeypatch)
    model=AsyncMock(side_effect=[PASS,TimeoutError('timeout')])
    monkeypatch.setattr(ollama,'structured',model)
    _,partial=asyncio.run(run())
    assert not partial['passed'] and not partial['checked']
    assert partial['verified_groups']==1 and partial['group_progress']['completed']==[0]
    model=AsyncMock(return_value=PASS);monkeypatch.setattr(ollama,'structured',model)
    _,done=asyncio.run(run(partial))
    assert done['passed'] and done['checked'] and done['verified_groups']==3
    assert done['resumed_groups']==1 and model.await_count==2

@pytest.mark.parametrize('change',['answer','facts','rules','buyer','product','instruction','expired','partition'])
def test_progress_cannot_approve_changed_inputs(monkeypatch,change):
    ollama,run=setup(monkeypatch)
    monkeypatch.setattr(ollama,'structured',AsyncMock(side_effect=[PASS,TimeoutError('timeout')]))
    _,partial=asyncio.run(run())
    changes={}
    if change=='answer':changes['reply']='Спасибо за Ваш отзыв! Рады, что упаковка понравилась.'
    elif change=='facts':changes['facts']=[{'text':'Новая характеристика'}]
    elif change=='rules':changes['rules']=['Учитывай замечание об упаковке.','Новое правило.','Не выдумывай свойства товара.']
    elif change=='buyer':changes['review']=SimpleNamespace(id='resume',text='Есть замечание',rating=4,risk='NORMAL',marketplace='wb')
    elif change=='product':changes['product']=SimpleNamespace(id='p',name='Другой товар',brand='B')
    elif change=='instruction':changes['instruction']='Учти новое пожелание'
    elif change=='expired':partial['group_progress']['expires_at']=0
    else:partial['group_progress']['partition']='changed'
    model=AsyncMock(return_value=PASS);monkeypatch.setattr(ollama,'structured',model)
    _,done=asyncio.run(run(partial,**changes))
    assert done['resumed_groups']==0 and model.await_count==3


def test_remaining_rule_violation_still_blocks_approval(monkeypatch):
    ollama,run=setup(monkeypatch)
    monkeypatch.setattr(ollama,'structured',AsyncMock(side_effect=[PASS,TimeoutError('timeout')]))
    _,partial=asyncio.run(run())
    model=AsyncMock(return_value={'compliant':False,'violations':['Нарушено правило'],'revised_answer':''})
    monkeypatch.setattr(ollama,'structured',model)
    _,done=asyncio.run(run(partial))
    assert not done['passed'] and done['verified_groups']==1 and model.await_count==1
