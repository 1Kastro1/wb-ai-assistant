"""Size complete model requests, including structured-output overhead."""
import json
import math

MAX_CONTEXT = 16384


def request_tokens(messages, schema=None, predict=512):
    # A more conservative Cyrillic estimate than bytes / 3. Include the schema,
    # chat-template overhead and output reserve, not only the owner rules.
    size = sum(len(message.get('content', '').encode('utf-8')) for message in messages)
    if schema:
        size += len(json.dumps(schema, ensure_ascii=False).encode('utf-8'))
    return math.ceil(size / 2) + predict + 512


def fitting_rule_groups(groups, system_message, payload, schema):
    """Split only between complete policies; never drop conditions or facts."""
    result = []

    def append(group):
        messages = [system_message, {'role': 'user', 'content': json.dumps(
            {**payload, 'owner_rules': group}, ensure_ascii=False) + '\n/no_think'}]
        needed = request_tokens(messages, schema)
        # Prefer small windows on CPU, but keep an indivisible whole policy.
        if needed <= 6144 or (len(group) == 1 and needed <= MAX_CONTEXT):
            result.append(group)
        elif len(group) > 1:
            middle = len(group) // 2
            append(group[:middle])
            append(group[middle:])
        else:
            raise ValueError('Правила слишком длинные: одно целое правило или данные отзыва не помещаются в контекст модели')

    for group in groups:
        append(group)
    return result


def draft_messages(system, payload, rules, facts, limit=6000):
    """Bound candidate preparation. Full policy verification is still required.

    Whole rules/fact records are included when they fit. This is not an approval
    prompt; the independent verifier receives the original complete policy and
    evidence. Never split a condition or silently truncate the buyer's message.
    """
    body = {**payload, 'owner_rules': [], 'verified_facts': []}
    def messages():
        return [{'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(body, ensure_ascii=False)}]
    # Reserve the provider's variant request and structured output schema.
    if request_tokens(messages(), predict=768) > MAX_CONTEXT - 1024:
        raise ValueError('Правила слишком длинные: исходные данные отзыва превышают контекст модели')
    for name, records in (('owner_rules', rules), ('verified_facts', facts)):
        for record in records:
            body[name].append(record)
            if request_tokens(messages(), predict=768) > limit - 1024:
                body[name].pop()
    return messages()
