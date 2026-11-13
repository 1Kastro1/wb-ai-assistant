import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.owner_reply_policy import enforce_owner_rules,attach_rule_quality
from app.rule_retry import technical_check
from app.rule_check_plan import prepare,request_json,step_deadline

PASS={'compliant':True,'violations':[],'revised_answer':''}


def inputs():
    return dict(reply='Добрый день! Спасибо, что написали. Для этой модели заявлены 55 Вт при 12 В.',
        review=SimpleNamespace(id='review',text='Может там и нет 55?',rating=4,risk='NORMAL',marketplace='yandex'),
        product=SimpleNamespace(id='lamp',name='Лампы H1 55W 12V'),
        rules=['Учитывай содержание вопроса.','Пиши тепло.','Не выдумывай характеристики.'],
        facts=[{'text':'Заявленные 55 Вт при 12 В','source':'Карточка товара'}],
        assess=lambda answer:{'passed':True},check_only=True,group_limit=1)


def test_each_durable_step_checks_one_whole_group_then_continues_without_timeout(monkeypatch):
    from app.integrations import ollama
    monkeypatch.setattr('app.owner_reply_policy.rule_groups',lambda rules:[[r] for r in rules])
    model=AsyncMock(return_value=PASS);monkeypatch.setattr(ollama,'structured',model)
    args=inputs();previous=None
    for completed in (1,2,3):
        _,result=asyncio.run(enforce_owner_rules(**args,previous_check=previous))
        assert result['verified_groups']==completed
        assert result['resumed_groups']==completed-1
        assert model.await_count==completed
        assert result['passed']==(completed==3)
        assert result['checked']==(completed==3)
        quality=attach_rule_quality({'passed':True},result)
        if completed<3:
            assert result['continuation'] and not result['technical_error']
            assert quality['owner_rules']['status']=='pending' and technical_check(quality)
            assert not quality['passed']
        previous=result
    for call in model.await_args_list:
        payload=json.loads(call.args[0][-1]['content'])
        assert payload['verified_facts']==args['facts']
        assert payload['buyer_message']==args['review'].text
        assert call.args[1]['properties']['revised_answer']['enum']==['']
        assert 120<=call.kwargs['timeout_seconds']<=300
    assert [json.loads(c.args[0][-1]['content'])['owner_rules'] for c in model.await_args_list]==[[r] for r in args['rules']]


def test_healthy_progress_never_approves_a_later_violation(monkeypatch):
    from app.integrations import ollama
    monkeypatch.setattr('app.owner_reply_policy.rule_groups',lambda rules:[[r] for r in rules])
    model=AsyncMock(side_effect=[PASS,{'compliant':False,'violations':['Не подтверждён факт'],'revised_answer':''}])
    monkeypatch.setattr(ollama,'structured',model)
    args=inputs();_,first=asyncio.run(enforce_owner_rules(**args))
    _,failed=asyncio.run(enforce_owner_rules(**args,previous_check=first))
    assert not failed['passed'] and failed['verified_groups']==1 and not failed.get('continuation')
    assert failed['issues']==['Не подтверждён факт']


def test_timeout_preserves_completed_steps_but_does_not_certify_them_as_whole_approval(monkeypatch):
    from app.integrations import ollama
    monkeypatch.setattr('app.owner_reply_policy.rule_groups',lambda rules:[[r] for r in rules])
    model=AsyncMock(side_effect=[PASS,TimeoutError('timeout'),PASS,PASS]);monkeypatch.setattr(ollama,'structured',model)
    args=inputs();previous=None
    for expected in (1,1,2,3):
        _,previous=asyncio.run(enforce_owner_rules(**args,previous_check=previous))
        assert previous['verified_groups']==expected
        assert previous['passed']==(expected==3)
    assert model.await_count==4


def test_step_deadline_is_sized_to_full_request_but_never_unbounded():
    schema={'type':'object'}
    small=step_deadline([{'content':'Короткий вопрос'}],schema,120)
    long=step_deadline([{'content':'Характеристика. '*500}],schema,120)
    huge=step_deadline([{'content':'я'*100000}],schema,120)
    assert 120<=small<long<=300 and huge==300


def test_check_only_plan_keeps_all_evidence_and_policies_but_cannot_request_a_rewrite():
    args=inputs()
    messages,payload,schema,groups=prepare(args['review'],args['product'],args['rules'],args['facts'],args['reply'],check_only=True)
    assert [rule for group in groups for rule in group]==args['rules']
    assert payload['verified_facts']==args['facts']
    assert schema['properties']['revised_answer']['enum']==['']
    assert json.loads(request_json(payload,groups[0]))['answer']==args['reply']
