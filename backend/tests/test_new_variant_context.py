from app.db import VerifiedReplyText,VerifiedReplyContext
import asyncio
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from app.db import Session, Product, Review, Draft, Memory, Setting
from app import services
from app.reply_policy import analyze_customer_message, semantic_reply_issues
from app.safety import review_intent
from app.learned_reply_templates import load_template, similar_candidate, remember_template
from app.owner_reply_rules import matching_owner_rules
from app.review_rule_pack143 import PACK

BUYER = 'Он не чистит кожу, вообще никак. Это полироль больше, чем бальзам. После него кожа блестит и ужасно скользит. Не совсем то, что нужно было.'
BAD = 'Здравствуйте. Благодарим за обратную связь. Рады, что покупка Вам понравилась. Пусть покупка и дальше радует Вас!\nС уважением ГК Юником.'


@pytest.mark.parametrize('rating', [1, 3, 5])
def test_buyer_words_override_stars(rating):
    analysis = analyze_customer_message(BUYER, rating)
    assert {'NO_RESULT', 'SLIPPERY_SURFACE'} <= set(analysis['complaints'])
    assert analysis['sentiment'] == 'negative'
    assert review_intent(BUYER) == 'NO_RESULT'
    review = SimpleNamespace(text=BUYER, rating=rating)
    product = SimpleNamespace(name='Лосьон очиститель кожи', category='', brand='KANGAROO')
    assert semantic_reply_issues(review, product, BAD)
    assert not services.assess_reply(review, product, BAD, [])['passed']


@pytest.mark.parametrize('text', ['Хорошо чистит кожу, не скользит.', 'Не слабо чистит кожу!', 'Раньше не чистил, этим средством доволен.'])
def test_negation_and_other_cleaner_are_not_the_screenshot_complaint(text):
    # The historical comparison is deliberately not used as a learned match.
    assert 'SLIPPERY_SURFACE' not in analyze_customer_message(text, 5)['complaints']


def seed(db, provider):
    db.add(Product(id='lotion', name='Лосьон очиститель для кожи автомобиля, кондиционер 500мл', category='Автохимия и очистители', brand='KANGAROO', marketplace=provider))
    db.add_all(Memory(id='rule'+str(i), text=r['text'], scope=r['scope'], kind='REVIEW_RULE') for i,r in enumerate(PACK['rules']))
    db.flush()
    db.add(Review(id='buyer', wb_review_id='buyer', wb_account_id='owner', product_id='lotion', marketplace=provider, text=BUYER, rating=5, risk='NORMAL', manual=False))
    db.flush()
    db.add(Draft(id='bad', review_id='buyer', text=BAD, original=BAD, quality={'passed':False}))
    db.commit()


@pytest.mark.parametrize('provider', ['wb', 'ozon', 'yandex'])
def test_new_variant_rotates_learns_and_reuses_only_success(client, monkeypatch, provider):
    from app.integrations import ollama
    checks = []
    async def verify(*a, **k):
        checks.append(True)
        return {'compliant':True, 'violations':[], 'revised_answer':''}
    def no_generation(*a, **k):
        raise AssertionError('Known lotion complaint has a contextual local answer')
    monkeypatch.setattr(ollama, 'structured', verify)
    monkeypatch.setattr(ollama, 'reply_variants', no_generation)
    with Session() as db:
        seed(db, provider)
        first = asyncio.run(services.generate_draft(db, 'buyer'))
        assert first['quality']['passed'], first['quality']
        assert 'скользкой' in first['text'] and 'очистки' in first['text']
        assert 'покупка Вам понравилась' not in first['text']
        assert 'тип кожи' in first['text']
        second = asyncio.run(services.generate_draft(db, 'buyer'))
        assert second['quality']['passed'], second['quality']
        assert second['text'] != first['text']
        assert list(db.scalars(select(VerifiedReplyContext)))
    with Session() as db:
        r,p=db.get(Review,'buyer'),db.get(Product,'lotion')
        rules=matching_owner_rules(db,p)
        facts=services.review_verified_facts(db,r,p,rules)
        assess=lambda value:services.assess_reply(r,p,value,facts)
        assert load_template(db,r,p,rules,facts,assess,previous=second['text'])[0] == first['text']
        r.text='Не очищает кожу. После обработки кожа стала скользкой.'
        db.commit()
        assert load_template(db,r,p,rules,facts,assess) is None
        assert similar_candidate(db,r,p,rules,facts,assess)
        before=len(checks)
        fresh=asyncio.run(services.generate_draft(db,'buyer'))
        assert fresh['model']=='learned-candidate-1.0'
        assert fresh['quality']['passed']
        assert len(checks)>before  # similar is an example, not an inherited approval
        assert similar_candidate(db,r,p,[*rules,'Новое требование'],facts,assess) is None


def test_failed_verification_never_teaches_template(client, monkeypatch):
    from app.integrations import ollama
    async def unavailable(*a, **k): raise TimeoutError()
    monkeypatch.setattr(ollama,'structured',unavailable)
    with Session() as db:
        seed(db,'wb')
        result=asyncio.run(services.generate_draft(db,'buyer'))
        assert not result['quality']['passed']
        assert result['quality']['owner_rules']['technical_error']
        assert not list(db.scalars(select(VerifiedReplyContext)))


def test_unchanged_verifier_repair_cannot_masquerade_as_new_variant(client, monkeypatch):
    from app.owner_reply_policy import apply_local_preferences, apply_signature
    with Session() as db:
        seed(db,'wb')
        p,r=db.get(Product,'lotion'),db.get(Review,'buyer')
        rules=matching_owner_rules(db,p)
        text,_=apply_local_preferences(BAD,r,rules)
        previous=apply_signature(text,rules)
        db.get(Draft,'bad').text=previous
        db.commit()
        async def unchanged(*a,**k):return previous, {'passed':False,'checked':False,'issues':['unavailable']}
        monkeypatch.setattr(services,'enforce_owner_rules',unchanged)
        with pytest.raises(ValueError,match='предыдущий текст сохранён'):
            asyncio.run(services.generate_draft(db,'buyer'))
        assert db.get(Draft,'bad').text==previous
        assert db.get(Draft,'bad').revision==1
