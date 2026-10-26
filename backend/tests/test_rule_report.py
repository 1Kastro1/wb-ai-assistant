import pytest
from app.db import Session, Draft, Review, Memory
from app.owner_reply_policy import fingerprint


@pytest.mark.parametrize('marketplace', ['wb', 'ozon', 'yandex', 'wb_question', 'ozon_question', 'yandex_question'])
def test_report_uses_current_audience_and_detects_changed_rules(client, seeded, marketplace):
    question = marketplace.endswith('_question')
    rule = 'Каждый ответ начинай с «Добрый день!».'
    with Session() as db:
        db.get(Review, 'r1').marketplace = marketplace
        db.get(Review, 'r1').text = ''
        db.get(Review, 'r1').rating = 5
        db.add_all([Memory(id='review-rule', kind='REVIEW_RULE', text=rule),
                    Memory(id='question-rule', kind='QUESTION_RULE', text=rule)])
        db.add(Draft(id='rules-detail', review_id='r1', text='Добрый день! Спасибо.', original='Добрый день! Спасибо.',
                     quality={'owner_rules': {'fingerprint': fingerprint([rule]), 'checked': True, 'passed': True}}))
        db.commit()
    result = client.get('/drafts/rules-detail/rules')
    assert result.status_code == 200
    assert result.json()['passed']
    assert result.json()['matched'] == 1
    assert result.json()['rules'][0]['status'] == 'verified'
    with Session() as db:
        db.get(Memory, 'question-rule' if question else 'review-rule').text = 'Всегда сообщай подтверждённый срок хранения.'
        db.commit()
    result = client.get('/drafts/rules-detail/rules').json()
    assert not result['passed']
    assert result['rules'][0]['status'] == 'pending'


def test_report_hides_foreign_store_drafts(client, seeded):
    with Session() as db:
        db.get(Review, 'r1').wb_account_id = 'foreign'
        db.add(Draft(id='foreign-draft', review_id='r1', text='Private', original='Private'))
        db.commit()
    assert client.get('/drafts/foreign-draft/rules').status_code == 404
