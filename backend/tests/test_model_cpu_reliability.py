import asyncio
from unittest.mock import AsyncMock
import httpx
import pytest
from sqlalchemy import select
from app.db import Session,Audit
from app.model_context import context_window
from app.generation_budget import GenerationBudget,MODEL_GENERATION_SECONDS,OWNER_RULE_CHECK_SECONDS
from app.security import EgressGuard


def test_ordinary_generation_and_checks_reuse_the_same_context_window():
    assert {context_window(size) for size in (1200,4096,4900,5120,6144,7100,8192)}=={8192}
    assert context_window(8193)==16384
    with pytest.raises(ValueError):context_window(16385)


def test_cpu_deadlines_fit_observed_responses_but_remain_bounded():
    assert 68<MODEL_GENERATION_SECONDS<=90
    assert 90<=OWNER_RULE_CHECK_SECONDS<=120


def test_real_provider_keeps_runner_options_stable_between_different_prompts(monkeypatch):
    from app.integrations import OllamaProvider,guard
    call=AsyncMock(return_value={'message':{'content':'готово'}})
    monkeypatch.setattr(guard,'request',call)
    for text in ('Короткий вопрос.', 'Характеристика товара. '*200):
        asyncio.run(OllamaProvider().chat([{'role':'user','content':text}],task='OWNER_RULES'))
    assert {args.kwargs['body']['options']['num_ctx'] for args in call.await_args_list}=={8192}
    assert all(args.kwargs['timeout_seconds']==120 for args in call.await_args_list)


def test_cancelled_network_request_is_not_reported_as_a_policy_block():
    async def delayed(request):
        await asyncio.Event().wait()
    guard=EgressGuard(httpx.MockTransport(delayed))
    async def run():
        with pytest.raises(TimeoutError):
            await GenerationBudget(.01).run(lambda:guard.request('ollama','chat',body={}))
    asyncio.run(run())
    with Session() as db:
        row=db.scalar(select(Audit).order_by(Audit.id.desc()))
        assert row.status=='CANCELLED' and row.provider=='ollama'
