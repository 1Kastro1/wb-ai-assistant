import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.db import Session, Product, Review, Memory
from app.owner_reply_rules import apply_owner_preferences, matching_owner_rules, owner_business_facts, apply_review_signature
from app.services import generate_draft
from app.safety import rule_reply, safe_fallback_reply, review_intent
from app.reply_policy import analyze_customer_message

GENERAL = 'Каждый ответ начинай с «Добрый день!». Если покупатель уже описал проблему, не спрашивай, что именно его не устроило.'
DISTRIBUTOR = 'При сомнениях в оригинальности сообщай: мы являемся официальным дистрибьютором EIKOSHA.'


def test_signature_is_idempotent_and_preserves_length_validation():
    from app.safety import validate_reply
    signature = 'С уважением ГК Юником.'
    rules = ['В конце каждого ответа на отзыв добавляй подпись «' + signature + '».']
    for suffix in ('', '\n\n' + signature, '\n' + signature + '\n' + signature):
        assert apply_review_signature('Спасибо!' + suffix, rules) == 'Спасибо!\n\n' + signature
    assert apply_review_signature('Спасибо!', []) == 'Спасибо!'
    body = 'А' * (5000 - len(signature) - 2)
    assert len(validate_reply(apply_review_signature(body, rules))) == 5000
    with pytest.raises(ValueError):
        validate_reply(apply_review_signature(body + 'А', rules))


@pytest.mark.parametrize('text', ['Слабо смывает жир', 'Плохо отмывает', 'Не удаляет жир'])
def test_weak_cleaning_recognized_and_uses_own_verified_steps(text):
    p = SimpleNamespace(name='Антижир для кухни 500 мл', brand='KANGAROO', category='', facts=[])
    r = SimpleNamespace(text=text, rating=3, risk='NORMAL')
    facts = [{'kind':'instruction', 'text':'Распылите средство. Подождите 3–5 минут. Смойте водой.', 'verification_status':'VERIFIED'}]
    assert review_intent(text) == 'NO_RESULT'
    assert 'NO_RESULT' in analyze_customer_message(text,3)['complaints']
    for answer in (rule_reply(r,p,facts=facts),safe_fallback_reply(r,p,facts=facts)):
        assert '3–5 минут' in answer and 'Смойте водой' in answer
        assert 'что именно' not in answer


def test_positive_negations_do_not_become_complaints():
    assert review_intent('Не слабо отмывает жир!') != 'NO_RESULT'
    assert review_intent('Это не подделка, оригинал!') != 'AUTHENTICITY'


def test_originality_claim_requires_enabled_owner_declaration_and_correct_format():
    p=SimpleNamespace(id='p',name='Сменный картридж TONIGHT V-94',brand='EIKOSHA',category='',facts=[])
    r=SimpleNamespace(text='Либо подделка либо айкоша уже не та',rating=1,risk='NORMAL')
    assert review_intent(r.text)=='AUTHENTICITY'
    answer=rule_reply(r,p,facts=owner_business_facts(p,[DISTRIBUTOR]))
    assert 'официальным дистрибьютором EIKOSHA' in answer
    assert 'GIGA' in answer and 'дефлектор' in answer and 'по салону' in answer
    assert 'баноч' not in answer and '58434122' not in answer
    assert 'дистрибьютор' not in rule_reply(r,p,facts=[])
    p.brand='OTHER'
    assert owner_business_facts(p,[DISTRIBUTOR])==[]


def test_preferences_cover_fallback_and_do_not_repeat_known_problem():
    r=SimpleNamespace(text='Слабо смывает жир',rating=3)
    answer,applied=apply_owner_preferences('Здравствуйте! Извините за результат. Уточните, пожалуйста, что именно Вас не устроило.',r,[GENERAL])
    assert answer.startswith('Добрый день!') and 'Уточните' not in answer
    assert len(applied)==2


def test_rules_match_case_insensitive_scope_and_exclude_disabled(client):
    with Session() as db:
        db.add_all([Memory(id='brand-rule',scope='brand:Eikosha',text=DISTRIBUTOR),Memory(id='off',scope='global',text='Disabled',enabled=False),Memory(id='foreign',scope='brand:OTHER',text='Foreign')]);db.commit()
        p=SimpleNamespace(id='p',brand='EIKOSHA',category='')
        assert matching_owner_rules(db,p)==[DISTRIBUTOR]


def test_generation_and_regeneration_keep_owner_rules_and_instruction_without_model(client,monkeypatch):
    from app import services
    model=AsyncMock(side_effect=AssertionError('Local verified guidance must not call the model'))
    monkeypatch.setattr(services.ollama,'reply_variants',model)
    with Session() as db:
        db.add(Memory(id='general',text=GENERAL,scope='global'))
        db.add(Product(id='cleaner',name='Антижир для кухни 500 мл',brand='KANGAROO',facts=[{'kind':'instruction','source':'Карточка товара','verification_status':'VERIFIED','text':'Распылите средство. Подождите 3–5 минут. Смойте водой.'}]))
        db.flush();db.add(Review(id='weak',wb_review_id='weak',product_id='cleaner',text='Слабо смывает жир',rating=3));db.commit()
        for _ in range(2):
            draft=asyncio.run(generate_draft(db,'weak'))
            assert draft['text'].startswith('Добрый день!') and '3–5 минут' in draft['text']
            assert 'что именно' not in draft['text']
            assert draft['quality']['owner_rules']['matched']==1
        model.assert_not_awaited()


def test_full_generation_originality_includes_store_declaration(client,monkeypatch):
    with Session() as db:
        db.add(Memory(id='distributor',text=DISTRIBUTOR,scope='brand:Eikosha'))
        db.add(Product(id='refill',name='Сменный картридж TONIGHT V-94',brand='EIKOSHA',facts=[]))
        db.flush();db.add(Review(id='origin',wb_review_id='origin',product_id='refill',text='Либо подделка либо айкоша уже не та',rating=1));db.commit()
        draft=asyncio.run(generate_draft(db,'origin'))
        assert 'официальным дистрибьютором' in draft['text'] and 'GIGA' in draft['text']
        assert any('правило владельца' in f['source'].lower() for f in draft['quality']['sources'])
