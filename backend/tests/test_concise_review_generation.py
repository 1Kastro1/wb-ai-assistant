import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from app.db import Session, Memory, User, Product, Review, Draft, Job
from app import services, owner_policy_cleanup as cleanup, owner_policy_concise as concise
from app.owner_reply_policy import enforce_owner_rules, local_issues
from app.rule_execution import positive_template, digest, PROFILES
from app.integrations import OllamaProvider, ollama


def seed_rules(db):
    for index, change in enumerate(concise.POLICY['changes']):
        before = next(c['text'] for c in cleanup.POLICY['changes'] if digest(c['text']) in change['old_hashes'])
        db.add(Memory(id='before-'+str(index), kind='REVIEW_RULE', text=before))
    db.add(Memory(id='signature', kind='REVIEW_RULE', text='В конце каждого ответа добавляй подпись «С уважением ГК Юником.».'))
    db.add(Memory(id='questions', kind='QUESTION_RULE', text='Правило вопросов не менять.'))
    db.add(User(id='owner-concise', username='owner-concise', password_hash='unused', role='owner', display_name='Owner'))
    db.add(Product(id='concise-product', name='Лампы автомобильные', brand='Brand'))
    db.flush()
    db.add(Review(id='concise-review', wb_review_id='concise-review', product_id='concise-product', rating=5, text=''))
    db.commit()


def test_three_rule_update_archives_exact_sources_and_preserves_questions_and_edits(client, tmp_path):
    with Session() as db:
        seed_rules(db)
        db.add(Draft(id='manual-concise', review_id='concise-review', text='Моя ручная правка', original='Исходный текст',
            revision=9, edits=[{'by':'owner-concise'}])); db.commit()
        result = cleanup.apply(db, 'owner-concise', tmp_path, policy=concise.POLICY)
        assert result['created'] == result['archived'] == 3
        assert len(json.loads(Path(result['backup']).read_text(encoding='utf-8'))['rules']) == 3
        assert db.get(Memory, 'questions').enabled
        assert db.get(Memory, 'questions').text == 'Правило вопросов не менять.'
        assert db.get(Draft, 'manual-concise').text == 'Моя ручная правка'
        assert db.get(Draft, 'manual-concise').revision == 9
        assert all(db.get(Job, jid).result['check_only'] for jid in result['verification_jobs'])
        assert cleanup.apply(db, 'owner-concise', tmp_path, policy=concise.POLICY)['changed'] == 0


def test_concise_update_refuses_to_overwrite_a_later_owner_rule_edit(client, tmp_path):
    with Session() as db:
        seed_rules(db)
        db.get(Memory, 'before-1').text += ' Добавлено владельцем.'; db.commit()
        with pytest.raises(ValueError, match='изменилось'):
            cleanup.apply(db, 'owner-concise', tmp_path, policy=concise.POLICY)
        assert all(row.enabled for row in db.scalars(select(Memory)))


@pytest.mark.parametrize('text', ['', 'Отличные лампочки.', 'Хорошая штука'])
def test_short_templates_pass_new_rules_without_model_and_unknown_rule_still_opts_out(client, monkeypatch, text):
    with Session() as db:
        seed_rules(db)
        row, product = db.get(Review, 'concise-review'), db.get(Product, 'concise-product')
        row.text = text
        rules = [c['text'] for c in concise.POLICY['changes']] + [db.get(Memory, 'signature').text]
        judge = AsyncMock(side_effect=AssertionError('No model for exact positive template'))
        monkeypatch.setattr(ollama, 'structured', judge)
        for revision in range(3):
            answer = positive_template(row, rules, revision, product=product)
            assert answer and len(answer) <= 150
            assert len(re.findall(r'спасибо|благодарим', answer, re.I)) == 1
            assert not local_issues(answer, rules, row)
            assert services.assess_reply(row, product, answer)['passed']
            _, verdict = asyncio.run(enforce_owner_rules(answer, row, product, rules, [],
                lambda value: services.assess_reply(row, product, value), allow_fast_positive=True, local_only=True))
            assert verdict['passed'] and verdict['verification_method'] == 'local_positive'
        assert positive_template(row, rules + ['Всегда сообщай номер партии.'], product=product) is None
        assert all(PROFILES[digest(rule)]['local_positive'] for rule in rules[:-1])


def test_duplicate_thanks_is_an_actual_local_rule_violation():
    answer = 'Добрый день! Спасибо за оценку. Благодарим за покупку.'
    assert 'Использовать одну благодарность без повторов' in local_issues(answer, ['Используй одну благодарность.'])


def test_explicit_refresh_only_replaces_exact_old_templates_and_keeps_manual_text(client, monkeypatch):
    from app import positive_recheck
    from app.rule_execution import LEGACY_POSITIVE_BODIES
    from app.owner_reply_policy import apply_signature
    with Session() as db:
        seed_rules(db)
        rules = [c['text'] for c in concise.POLICY['changes']] + [db.get(Memory, 'signature').text]
        for row in db.scalars(select(Memory).where(Memory.kind == 'REVIEW_RULE')):
            row.enabled = False
        for i, text in enumerate(rules):
            db.add(Memory(id='new-'+str(i), kind='REVIEW_RULE', text=text))
        old = apply_signature('Добрый день! '+LEGACY_POSITIVE_BODIES[0], rules)
        db.add(Draft(id='legacy-concise', review_id='concise-review', text=old, original=old, quality={'passed':True}))
        db.add(Review(id='human-review', wb_review_id='human-review', product_id='concise-product', text='', rating=5))
        db.flush()
        db.add(Draft(id='human-draft', review_id='human-review', text=old, original=old, revision=8,
            edits=[{'by':'employee'}], quality={'passed':True}))
        db.commit()
        job = positive_recheck.enqueue(db, 'owner-concise', 'owner', refresh_templates=True)
        jid = job.id
    judge = AsyncMock(side_effect=AssertionError('Refresh is model-free'))
    monkeypatch.setattr(ollama, 'structured', judge)
    asyncio.run(positive_recheck.step(jid))
    with Session() as db:
        assert db.get(Job, jid).status == 'completed'
        assert db.get(Job, jid).result['changed'] == 1
        assert len(db.get(Draft, 'legacy-concise').text) < len(old)
        assert db.get(Draft, 'human-draft').text == old and db.get(Draft, 'human-draft').revision == 8
    judge.assert_not_awaited()


def test_known_model_outage_saves_batch_draft_and_queues_check_without_second_model_deadline(client, monkeypatch):
    with Session() as db:
        seed_rules(db)
        row = db.get(Review, 'concise-review'); row.rating = 2; row.text = 'Покупкой разочарован'; db.commit()
        writer = AsyncMock(side_effect=ConnectionError('offline'))
        checker = AsyncMock(side_effect=AssertionError('No second model request during known outage'))
        monkeypatch.setattr(ollama, 'reply_variants', writer)
        monkeypatch.setattr(ollama, 'structured', checker)
        result = asyncio.run(services.generate_draft(db, row.id, skip_existing=True))
        assert result['text'] and result['quality']['owner_rules']['technical_error']
        assert not result['quality']['owner_rules']['passed']
        assert result['quality']['owner_rules']['checked'] is False
        assert db.scalar(select(Job).where(Job.kind == 'draft_recheck')).result['check_only']
        assert db.get(Review, row.id).status == 'manual_review'
        writer.assert_awaited_once(); checker.assert_not_awaited()


@pytest.mark.parametrize('raw', ['{"variants":[{}]}', '{"variants":[]}', '{"variants":["unfinished', '', '```json\n{}\n```'])
def test_broken_model_json_is_never_saved_as_a_customer_answer(client, monkeypatch, raw):
    provider = OllamaProvider()
    monkeypatch.setattr(provider, 'chat', AsyncMock(return_value=raw))
    with pytest.raises(ValueError, match='Некорректный ответ модели'):
        asyncio.run(provider.reply_variants([{'role':'user','content':'Отзыв'}], 1))
