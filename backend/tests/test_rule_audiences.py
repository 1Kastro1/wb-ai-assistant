import asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy import select, func
from app.db import Session, Memory, Product, Review, Draft, Action, setting
from app.owner_reply_rules import matching_owner_rules
from app.split_rule_audiences import split_legacy_rules


SIGNATURE = 'В конце каждого ответа на отзыв добавляй подпись «С уважением ГК Юником.».'
UNIQOM = 'При вопросе с VIN или Frame сначала проверяй автомобиль через поле «Поиск по VIN / Frame» оригинальных каталогов Uniqom.'


def test_proposal_confirmation_and_move_preserve_kind_scope_and_audit(client):
    for kind in ('REVIEW_RULE', 'QUESTION_RULE'):
        proposed = client.post('/memory/propose', json={'text': 'Ответ по существу', 'kind': kind, 'scope': 'brand:EIKOSHA'})
        assert proposed.status_code == 200
        assert proposed.json()['payload']['kind'] == kind
        confirmed = client.post('/actions/' + proposed.json()['id'] + '/confirm', json={})
        assert confirmed.status_code == 200, confirmed.text
    rules = client.get('/memory').json()
    assert {rule['kind'] for rule in rules} == {'REVIEW_RULE', 'QUESTION_RULE'}
    moved = client.patch('/memory/' + rules[0]['id'] + '/kind', json={'kind': 'QUESTION_RULE'})
    assert moved.status_code == 200
    assert moved.json()['kind'] == 'QUESTION_RULE' and moved.json()['scope'] == 'brand:EIKOSHA'
    assert client.patch('/memory/' + rules[0]['id'] + '/kind', json={'kind': 'invalid'}).status_code == 422
    assert client.patch('/memory/missing/kind', json={'kind': 'REVIEW_RULE'}).status_code == 404
    history = client.get('/activity/page').json()['items']
    assert any(row['details'].get('event') == 'rule_updated' and row['details'].get('kind') == 'QUESTION_RULE' for row in history)


def test_new_default_and_chat_rules_have_one_explicit_audience(client):
    default = client.post('/memory/propose', json={'text': 'Пиши кратко'}).json()
    assert default['payload']['kind'] == 'REVIEW_RULE'
    question = client.post('/chat', json={'text': 'Запомни для вопросов: отвечай по подтверждённым сведениям'}).json()
    assert question['data']['payload']['kind'] == 'QUESTION_RULE'


def test_matching_separates_enabled_rules_without_losing_brand_and_product_scope(client):
    with Session() as db:
        p = Product(id='p', name='Товар', brand='EIKOSHA', category='Ароматизатор')
        db.add(p)
        db.add_all([Memory(id='r', text='Правило отзывов', kind='REVIEW_RULE', scope='brand:eikosha'),
                    Memory(id='q', text='Правило вопросов', kind='QUESTION_RULE', scope='product:p'),
                    Memory(id='legacy', text='Старое общее правило', scope='global'),
                    Memory(id='off', text='Отключено', kind='QUESTION_RULE', enabled=False),
                    Memory(id='foreign', text='Другой бренд', kind='QUESTION_RULE', scope='brand:OTHER')])
        db.commit()
        assert set(matching_owner_rules(db, p)) == {'Правило отзывов', 'Старое общее правило'}
        assert set(matching_owner_rules(db, p, for_questions=True)) == {'Правило вопросов', 'Старое общее правило'}


def test_legacy_split_is_idempotent_and_keeps_independent_disabled_and_scoped_rules(client):
    with Session() as db:
        db.add_all([Memory(id='sig', text=SIGNATURE), Memory(id='uniqom', text=UNIQOM),
                    Memory(id='greeting', text='Каждый ответ начинай с «Добрый день!».') ,
                    Memory(id='brand', text='Правило бренда', scope='brand:EIKOSHA', enabled=False)])
        db.commit()
        assert len(split_legacy_rules(db)) == 4
        db.commit()
        assert split_legacy_rules(db) == []
        rules = list(db.scalars(select(Memory)))
        assert len(rules) == 6
        assert db.get(Memory, 'uniqom').kind == 'QUESTION_RULE'
        assert db.get(Memory, 'sig').kind == 'REVIEW_RULE'
        brand_rules = [rule for rule in rules if rule.text == 'Правило бренда']
        assert len(brand_rules) == 2 and all(not rule.enabled and rule.scope == 'brand:EIKOSHA' for rule in brand_rules)
        brand_rules[0].enabled = True
        assert not brand_rules[1].enabled
        assert len(setting(db, 'rule-audience-migration:v1')) == 1


def test_question_preview_with_frame_is_not_a_review_and_has_no_side_effects(client):
    with Session() as db:
        db.add(Memory(id='sig', text=SIGNATURE, kind='REVIEW_RULE'))
        db.add(Memory(id='hello', text='Каждый ответ начинай с «Добрый день!».', kind='QUESTION_RULE'))
        db.commit()
        before = {model: db.scalar(select(func.count()).select_from(model)) for model in (Review, Draft, Action)}
    with patch('app.integrations.ollama.chat', new=AsyncMock(side_effect=AssertionError('VIN previews must not invent a catalog lookup'))) as model:
        result = client.post('/memory/preview', json={'kind': 'QUESTION_RULE', 'text': UNIQOM, 'example': 'Подойдёт RD1-5300053?'} )
        assert result.status_code == 200, result.text
        body = result.json()
        assert body['kind'] == 'QUESTION_RULE'
        assert body['quality']['intent_label'] == 'совместимость'
        assert 'спасибо за отзыв' not in body['text'].lower()
        assert 'С уважением ГК Юником.' not in body['text']
        assert 'RD1-5300053' not in body['text']
        assert 'пока нет подтверждённого подбора' in body['text']
        assert 'не открывает Uniqom' in body['note']
        model.assert_not_awaited()
    with Session() as db:
        assert all(db.scalar(select(func.count()).select_from(model)) == count for model, count in before.items())
        assert setting(db, 'fitment-salt:owner', '') == ''


def test_question_model_preview_receives_only_question_rules(client):
    with Session() as db:
        db.add_all([Memory(id='review', text='REVIEW_ONLY_SENTINEL', kind='REVIEW_RULE'),
                    Memory(id='question', text='QUESTION_ONLY_SENTINEL', kind='QUESTION_RULE')])
        db.commit()
    model = AsyncMock(return_value='Здравствуйте! В карточке товара недостаточно подтверждённых сведений о материале корпуса.')
    with patch('app.integrations.ollama.chat', new=model):
        response = client.post('/memory/preview', json={'kind': 'QUESTION_RULE', 'text': 'Не выдумывай материал', 'example': 'Из чего корпус?'})
        assert response.status_code == 200, response.text
        prompt = str(model.call_args)
        assert 'QUESTION_ONLY_SENTINEL' in prompt
        assert 'REVIEW_ONLY_SENTINEL' not in prompt
        assert 'review_data' not in prompt


def test_review_preview_filters_question_rules_and_keeps_signature_during_model_outage(client):
    with Session() as db:
        db.add(Memory(id='question', text='QUESTION_ONLY_SENTINEL', kind='QUESTION_RULE'))
        db.commit()
    model = AsyncMock(side_effect=RuntimeError('offline'))
    with patch('app.integrations.ollama.chat', new=model):
        response = client.post('/memory/preview', json={'kind': 'REVIEW_RULE', 'text': SIGNATURE, 'example': 'Спасибо, хороший товар!', 'rating': 5})
        assert response.status_code == 200, response.text
        assert response.json()['text'].endswith('С уважением ГК Юником.')
        assert 'QUESTION_ONLY_SENTINEL' not in str(model.call_args)
