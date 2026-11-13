import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.model_context import fitting_rule_groups, request_tokens, MAX_CONTEXT


def test_batches_count_facts_schema_and_cyrillic_without_losing_policies():
    rules = ['Если есть замечание ' + str(i) + ':\n' + 'учитывай подтверждённые сведения. ' * 100 for i in range(4)]
    system = {'role': 'system', 'content': 'Check every policy.'}
    payload = {'buyer_message': 'На удивление отличные щётки.', 'answer': 'Спасибо за отзыв.',
               'verified_facts': [{'text': 'Подтверждённые данные. ' * 200}]}
    schema = {'description': 'Проверка. ' * 200}
    groups = fitting_rule_groups([rules], system, payload, schema)
    assert len(groups) > 1
    assert [rule for group in groups for rule in group] == rules
    for group in groups:
        messages = [system, {'role': 'user', 'content': json.dumps(
            {**payload, 'owner_rules': group}, ensure_ascii=False) + '\n/no_think'}]
        assert request_tokens(messages, schema) <= MAX_CONTEXT
        assert payload['verified_facts'][0]['text'] in messages[-1]['content']


def test_single_oversized_policy_is_not_silently_cut():
    with pytest.raises(ValueError, match='одно целое правило'):
        fitting_rule_groups([['Если повреждено:\n' + 'условие. ' * 5000]],
                            {'role': 'system', 'content': 'Check'}, {}, {})


def test_schema_overhead_changes_requested_context(monkeypatch):
    from app.integrations import ollama, guard
    from app.model_router import ModelRouter
    monkeypatch.setattr(ModelRouter, 'choose', lambda self, task: {
        'model': 'test', 'options': {'num_ctx': 4096, 'num_predict': 512}})
    request = AsyncMock(return_value={'message': {'content': '{}'}})
    monkeypatch.setattr(guard, 'request', request)
    schema = {'description': 'Схема проверки. ' * 300}
    asyncio.run(ollama.chat([{'role': 'user', 'content': 'Русский текст. ' * 300}],
                           schema, task='OWNER_RULES'))
    body = request.call_args.kwargs['body']
    assert body['options']['num_ctx'] >= request_tokens(body['messages'], schema)


def test_truncated_model_verdict_is_discarded_and_full_input_is_retried(monkeypatch):
    from app.integrations import ollama, guard
    from app.model_router import ModelRouter
    monkeypatch.setattr(ModelRouter, 'choose', lambda self, task: {
        'model': 'test', 'options': {'num_ctx': 4096, 'num_predict': 512}})
    requests = []

    async def request(*args, body, **kwargs):
        requests.append(body)
        if len(requests) == 1:
            return {'prompt_eval_count': body['options']['num_ctx'] - 1,
                    'message': {'content': 'TRUNCATED APPROVAL'}}
        return {'prompt_eval_count': 200, 'message': {'content': 'FULL VERDICT'}}

    monkeypatch.setattr(guard, 'request', request)
    value = asyncio.run(ollama.chat([{'role': 'user', 'content': 'Полное правило.'}], task='OWNER_RULES'))
    assert value == 'FULL VERDICT'
    assert requests[0]['messages'] == requests[1]['messages']
    assert requests[1]['options']['num_ctx'] > requests[0]['options']['num_ctx']


def test_context_retry_stops_at_model_limit(monkeypatch):
    from app.integrations import ollama, guard
    from app.model_router import ModelRouter
    monkeypatch.setattr(ModelRouter, 'choose', lambda self, task: {
        'model': 'test', 'options': {'num_ctx': MAX_CONTEXT, 'num_predict': 512}})
    request = AsyncMock(return_value={'prompt_eval_count': MAX_CONTEXT,
                                     'message': {'content': 'UNSAFE APPROVAL'}})
    monkeypatch.setattr(guard, 'request', request)
    with pytest.raises(ValueError, match='переполнен'):
        asyncio.run(ollama.chat([{'role': 'user', 'content': 'Правило.'}], task='OWNER_RULES'))
    assert request.await_count == 1


@pytest.mark.parametrize('fail_last', [False, True])
def test_large_context_checks_every_group_and_never_accepts_partial_success(monkeypatch, fail_last):
    from app.integrations import ollama
    from app.owner_reply_policy import enforce_owner_rules
    from app.rule_execution import clear_verdicts
    clear_verdicts()
    rules = ['Требование ' + str(i) + ': ' + 'соблюдай условия. ' * 150 for i in range(4)]
    facts = [{'text': 'Известный факт. ' * 600}]
    seen = []

    async def check(messages, schema, **kwargs):
        assert request_tokens([*messages[:-1], {**messages[-1],
            'content': messages[-1]['content'] + '\n/no_think'}], schema) <= MAX_CONTEXT
        payload = json.loads(messages[-1]['content'])
        assert payload['verified_facts'] == facts
        seen.extend(payload['owner_rules'])
        if fail_last and rules[-1] in payload['owner_rules']:
            raise TimeoutError()
        return {'compliant': True, 'violations': [], 'revised_answer': ''}

    monkeypatch.setattr(ollama, 'structured', check)
    _, quality = asyncio.run(enforce_owner_rules('Здравствуйте. Спасибо за отзыв.',
        SimpleNamespace(text='На удивление отличные щётки.', rating=5, marketplace='yandex'),
        SimpleNamespace(name='Щётка Avantech'), rules, facts, lambda text: {'passed': True}, check_only=True))
    assert seen == rules
    assert quality['semantic_groups'] > 1
    assert quality['passed'] is (not fail_last)
    assert quality['checked'] is (not fail_last)
    assert quality['verified_groups'] == quality['semantic_groups'] - int(fail_last)
