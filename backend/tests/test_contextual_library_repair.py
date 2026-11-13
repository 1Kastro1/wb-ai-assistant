import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from app.auto_review_repair import repair


def sample():
    return (SimpleNamespace(id='review',text='Упаковка целая, но распылитель не работает',rating=4,
        risk='NORMAL',manual=False,safety_critical=False,marketplace='yandex'),
        SimpleNamespace(id='product',name='Очиститель',brand='Бренд',category='Автохимия'))


def test_bad_fallback_gets_one_contextual_candidate_before_expensive_verification(monkeypatch):
    review,product=sample()
    monkeypatch.setattr('app.safety.safe_fallback_reply',lambda *a,**k:'Общая благодарность')
    monkeypatch.setattr('app.review_cases.specific_review_case',lambda *a:None)
    model=AsyncMock(return_value=['Извините, что распылитель не работает. Проверьте фиксацию насадки.'])
    monkeypatch.setattr('app.integrations.ollama.reply_variants',model)
    async def verify(answer,*args,**kwargs):
        assert answer.startswith('Извините')
        return answer,{'passed':True,'checked':True,'issues':[]}
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',verify)
    assess=lambda value:{'passed':value.startswith('Извините'),'issues':[] if value.startswith('Извините') else ['ответить именно на содержание отзыва']}
    answer,quality=asyncio.run(repair(review,product,[],[],assess))
    assert quality['passed'] and quality['automatic_repair']['contextual_generation']
    assert model.await_count==1 and model.call_args.kwargs['count']==1
    assert 'buyer_message' in model.call_args.args[0][1]['content']


def test_local_template_probe_never_starts_generation(monkeypatch):
    review,product=sample()
    model=AsyncMock(side_effect=AssertionError('local-only probe must not use a model'))
    monkeypatch.setattr('app.integrations.ollama.reply_variants',model)
    from app.generation_control import FastPathUnavailable
    async def no_local(*args,**kwargs):raise FastPathUnavailable()
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',no_local)
    assert asyncio.run(repair(review,product,[],[],lambda value:{'passed':False,'issues':['bad']},local_only=True)) is None
    assert model.await_count==0
