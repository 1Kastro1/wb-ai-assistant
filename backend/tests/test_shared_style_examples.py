from app.db import VerifiedReplyText,VerifiedReplyContext
import asyncio
from types import SimpleNamespace as NS
import pytest
from app import services
from app.db import Session, Product, Review, Draft, Memory, Setting, set_setting
from app.learned_reply_templates import remember_template
from app.owner_reply_policy import enforce_owner_rules, attach_rule_quality, apply_local_preferences, local_issues
from app.owner_reply_rules import matching_owner_rules
from app.reply_identity import normalize_reply_opening
from app.review_rule_pack143 import PACK
from app.rule_execution import positive_template


def seed(db, market):
    product = Product(id='p', name='Ароматизатор', brand='EIKOSHA', marketplace=market)
    db.add(product)
    db.add_all(Memory(id='rule-'+str(i), kind='REVIEW_RULE', scope=r['scope'], text=r['text'])
               for i, r in enumerate(PACK['rules']))
    db.flush()
    review = Review(id='r', wb_review_id='remote', wb_account_id='owner', product_id='p',
                    rating=5, text='Приятный запах.', marketplace=market, risk='NORMAL', manual=False)
    db.add(review)
    db.flush()
    rules = matching_owner_rules(db, product)
    facts = services.review_verified_facts(db, review, product, rules)
    answer = positive_template(review, rules, product=product)
    assess = lambda value: services.assess_reply(review, product, value, facts)
    answer, verdict = asyncio.run(enforce_owner_rules(answer, review, product, rules, facts,
                                                     assess, allow_fast_positive=True))
    assert verdict['passed'] and verdict['checked']
    draft = Draft(id='d', review_id='r', text=answer, original=answer,
                  quality=attach_rule_quality(assess(answer), verdict))
    db.add(draft)
    db.flush()
    assert remember_template(db, review, product, rules, facts, draft)
    db.commit()
    return review, product, draft


@pytest.mark.parametrize('market', ['wb', 'ozon', 'yandex'])
def test_examples_require_current_verified_library_not_publication(client, market):
    with Session() as db:
        review, product, draft = seed(db, market)
        found = services.previous_answer_examples(db, review, product)
        assert found[0]['answer'] == draft.text
        assert found[0]['source'] == 'verified_current_context'
        review.existing_answer = 'Мария, здравствуйте. Документы отправлены, Вам всё вернут за 3 дня.'
        review.is_answered = True
        db.commit()
        # Publication and its text do not replace the independently verified example.
        assert services.previous_answer_examples(db, review, product)[0]['answer'] == draft.text
        review.text = 'Приятный запах, но упаковка повреждена'
        assert services.previous_answer_examples(db, review, product) == []
        review.text = 'Приятный запах.'
        product.facts = [{'text': 'Изменённые сведения', 'verification_status': 'VERIFIED'}]
        assert services.previous_answer_examples(db, review, product) == []
        product.facts = []
        db.add(Memory(id='new-rule', kind='REVIEW_RULE', scope='global', text='Не обещай действий сотрудников.'))
        db.flush()
        assert services.previous_answer_examples(db, review, product) == []


def test_failed_verdict_and_changed_name_are_not_examples(client):
    with Session() as db:
        review, product, draft = seed(db, 'ozon')
        review.buyer_name = 'Мария'
        assert services.previous_answer_examples(db, review, product) == []
        del review.buyer_name
        # A library entry with a failed check cannot become a generation example.
        from sqlalchemy import select
        for row in db.scalars(select(VerifiedReplyContext)):
            row.verdict={**row.verdict,'passed':False,'technical_error':True}
        db.flush()
        assert services.previous_answer_examples(db, review, product) == []


def test_stale_observed_style_cannot_override_active_style_defaults(client):
    with Session() as db:
        set_setting(db, 'learned_reply_style:all-marketplaces',
                    {'answered_count': 0, 'owner_preferences': ['эмодзи', '3–5 предложений']})
        db.commit()
        styles = [services.shared_reply_style(db) for _ in ('wb', 'ozon', 'yandex')]
        assert styles[0] == styles[1] == styles[2]
        preferences = ' '.join(styles[0]['owner_preferences'])
        assert 'Без эмодзи' in preferences and 'два тёплых предложения' in preferences
        assert '3–5' not in preferences
        assert 'приоритет' in styles[0]['authority']


@pytest.mark.parametrize('opening', [
    'Мария, здравствуйте. Спасибо за отзыв.',
    'Здравствуйте, Мария! Спасибо за отзыв.',
    'Здравствуйте. Мария, спасибо за отзыв.',
    'Добрый день! Мария, спасибо за отзыв.',
])
def test_anonymous_opening_does_not_inherit_name_even_with_greeting_alternatives(opening):
    review = NS(text='Приятный запах', rating=5, marketplace='wb', buyer_name_hidden=True)
    rules = [r['text'] for r in PACK['rules'] if r['text'].startswith('Стиль и формат')]
    assert 'неподтверждённое имя' in ' '.join(local_issues(opening, rules, review))
    cleaned, _ = apply_local_preferences(opening, review, rules)
    assert 'Мария' not in cleaned
    assert cleaned.count('дравствуйте') + cleaned.count('Добрый день') == 1
    assert not local_issues(cleaned, rules, review)


@pytest.mark.parametrize('answer', ['Здравствуйте! Спасибо, что написали.',
                                  'Добрый день! Рады, что аромат понравился.',
                                  'Здравствуйте. Для проверки нужна маркировка.'])
def test_name_cleanup_keeps_regular_sentences(answer):
    assert normalize_reply_opening(answer, NS()) == answer


def test_identity_helper_preserves_confirmed_name_but_not_hidden_name():
    answer = 'Мария, здравствуйте. Спасибо за отзыв.'
    assert normalize_reply_opening(answer, NS(buyer_name='Мария')) == answer
    assert 'Мария' not in normalize_reply_opening(answer, NS(buyer_name='Мария', buyer_name_hidden=True))
