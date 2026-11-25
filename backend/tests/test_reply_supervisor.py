import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import model_router, reply_supervisor
from app.db import Session, set_setting
from app.skill_engine import ENABLED, ReplySkills


@pytest.mark.parametrize('kind,rating,result,status', [
    ('review', 2, {'issues': ['unsupported_claim']}, 'attention'),
    ('question', 0, {'issues': []}, 'clear'),
    ('question', 0, {'issues': ['fabricated_code']}, 'unavailable'),
    ('question', 0, TimeoutError(), 'unavailable'),
])
def test_supervisor_records_exact_draft_result_without_granting_approval(monkeypatch, kind, rating, result, status):
    monkeypatch.setattr(model_router, 'SUPERVISOR_MODEL', 'gemma3:4b')
    monkeypatch.setattr(reply_supervisor, 'SUPERVISOR_MODEL', 'gemma3:4b')
    call = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)
    monkeypatch.setattr(reply_supervisor.ollama, 'structured', call)
    with Session() as db:
        set_setting(db, ENABLED, True)
        skills = ReplySkills(db, kind)
        quality = {'passed': True}
        inquiry = SimpleNamespace(text='Подойдёт ли деталь?', rating=rating, risk='NORMAL')
        asyncio.run(reply_supervisor.observe(skills, inquiry, 'Уточните модель.', [{'text': 'Артикул A10'}], quality))
        assert quality['passed'] is True
        assert quality['reply_supervisor']['status'] == status
        assert skills.steps['reply_supervisor']['status'] == ('error' if status == 'unavailable' else 'completed')
        args, kwargs = call.await_args
        assert kwargs['task'] == 'REPLY_SUPERVISOR'
        assert json.loads(args[0][-1]['content'])['answer'] == 'Уточните модель.'
        assert model_router.ModelRouter().choose(kwargs['task'])['model'] == 'gemma3:4b'


def test_simple_praise_and_unverified_drafts_do_not_use_extra_model(monkeypatch):
    monkeypatch.setattr(model_router, 'SUPERVISOR_MODEL', 'gemma3:4b')
    call = AsyncMock()
    monkeypatch.setattr(reply_supervisor.ollama, 'structured', call)
    with Session() as db:
        set_setting(db, ENABLED, True)
        skills = ReplySkills(db, 'review')
        review = SimpleNamespace(text='Отлично!', rating=5, risk='NORMAL')
        asyncio.run(reply_supervisor.observe(skills, review, 'Спасибо!', [], {'passed': True}))
        review.rating = 2
        asyncio.run(reply_supervisor.observe(skills, review, 'Спасибо!', [], {'passed': False}))
    call.assert_not_awaited()


@pytest.mark.parametrize('tag', ['', 'gpt-oss:120b-cloud', 'other/model:3b'])
def test_router_requires_local_supervisor_model(monkeypatch, tag):
    monkeypatch.setattr(model_router, 'SUPERVISOR_MODEL', tag)
    with pytest.raises(ValueError):
        model_router.ModelRouter().choose('REPLY_SUPERVISOR')
