"""Availability retries must not loop over an unchanged invalid request."""
import asyncio
import json
from types import SimpleNamespace as S
from unittest.mock import AsyncMock

import pytest
import httpx
from sqlalchemy import select
from app.db import Session, Product, Review, Draft, Job, Memory
from app import integrations, services, owner_reply_policy
from app.rule_retry import verification_failure, technical_check, enqueue
from app.draft_audit import recheck_step, automatic_check_condition


@pytest.mark.parametrize('error,retryable,code', [
    (TimeoutError('deadline'),True,'timeout'),
    (ConnectionError('connection'),True,'service_unavailable'),
    (ValueError('HTTP 429'),True,'service_unavailable'),
    (ValueError('HTTP 503'),True,'service_unavailable'),
    (json.JSONDecodeError('bad JSON','{',1),True,'invalid_model_response'),
    (ValueError('Некорректный результат проверки правил'),True,'invalid_model_response'),
    (ValueError('Правила слишком длинные для контекста модели'),False,'context_limit'),
    (ValueError('Контекст модели переполнен; сократите повторы'),False,'context_limit'),
    (ValueError('HTTP 401'),False,'service_configuration'),
    (ValueError('HTTP 422'),False,'service_configuration'),
    (TypeError('incorrect argument'),False,'verification_error')])
def test_failure_classification(error,retryable,code):
    assert verification_failure(error)['retryable'] is retryable
    assert verification_failure(error)['failure_code'] == code


@pytest.mark.parametrize('status,retryable',[(401,False),(403,False),(429,True),(503,True)])
def test_http_status_exception_is_classified_without_relying_on_error_text(status,retryable):
    request=httpx.Request('POST','https://model.invalid/check')
    error=httpx.HTTPStatusError('request failed',request=request,response=httpx.Response(status,request=request))
    assert verification_failure(error)['retryable'] is retryable


def test_permanent_verification_failure_stops_automatic_retry_without_approving_or_rewriting(client, monkeypatch):
    rule='Поблагодари покупателя за подробное описание доставки.'
    reply='Здравствуйте! Спасибо за подробное описание доставки.'
    with Session() as db:
        db.add(Product(id='p',name='Товар',brand='TEST'));db.flush()
        db.add(Review(id='r',wb_review_id='r',product_id='p',text='Доставили вовремя',rating=5));db.flush()
        db.add(Memory(id='rule',kind='REVIEW_RULE',text=rule))
        draft=Draft(id='d',review_id='r',text=reply,original=reply,revision=9,
            quality={'passed':False,'owner_rules':{'passed':False,'checked':False,'technical_error':True}})
        db.add(draft);db.flush();job=enqueue(db,draft,db.get(Review,'r'));jid=job.id;db.commit()
    model=AsyncMock(side_effect=ValueError('Правила слишком длинные для контекста модели'))
    monkeypatch.setattr(integrations.ollama,'structured',model)
    monkeypatch.setattr(services,'assess_reply',lambda *a,**k:{'passed':True,'issues':[],'score':96})
    asyncio.run(recheck_step(jid))
    with Session() as db:
        draft=db.get(Draft,'d');rules=draft.quality['owner_rules']
        assert (draft.text,draft.revision,draft.edits) == (reply,9,[])
        assert rules['status']=='blocked' and rules['retryable'] is False
        assert rules['failure_code']=='context_limit' and not rules['checked'] and not rules['passed']
        assert not technical_check(draft.quality) and not draft.quality['passed']
        assert db.get(Job,jid).status=='completed'
        assert db.scalar(select(Draft.id).join(Review).where(automatic_check_condition())) is None
        assert not db.get(Review,'r').is_answered
    assert model.await_count==1


def test_changed_rules_clear_permanent_failure_but_do_not_inherit_success():
    old_rules=['Пиши развёрнутый ответ о доставке.']
    new_rules=['Поблагодари покупателя за описание доставки.']
    previous={'fingerprint':owner_reply_policy.fingerprint(old_rules),'passed':False,'checked':False,
              'retryable':False,'failure_code':'context_limit','status':'blocked',
              'technical_error':False,'issues':['Объём правил превышает контекст']}
    result=owner_reply_policy.saved_rule_quality('Здравствуйте! Спасибо за описание доставки.',new_rules,previous)
    assert not result['passed'] and not result['checked']
    assert result['fingerprint']==owner_reply_policy.fingerprint(new_rules)
    assert not ({'retryable','failure_code','status','technical_error'} & result.keys())


def test_failed_check_is_not_a_rule_violation(monkeypatch):
    monkeypatch.setattr(integrations.ollama,'structured',AsyncMock(side_effect=ValueError('HTTP 403')))
    answer, rules=asyncio.run(owner_reply_policy.enforce_owner_rules(
        'Здравствуйте! Спасибо за описание доставки.',
        S(text='Доставили вовремя',rating=5,marketplace='wb'),S(name='Товар'),
        ['Поблагодари за описание доставки.'],[],lambda value:{'passed':True},check_only=True))
    assert answer=='Здравствуйте! Спасибо за описание доставки.'
    assert not rules['checked'] and not rules['passed'] and rules['violations']==[]
