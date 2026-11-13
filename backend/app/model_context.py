"""Size complete model requests, including structured-output overhead."""
import json
import math

MAX_CONTEXT = 16384


def context_window(needed, minimum=0):
    """Reuse one warm 8K runner for ordinary requests; grow only to 16K.

    Ollama 0.11 reloads the runner when num_ctx changes. Rounding each request
    to a different 1K boundary repeatedly spent the CPU deadline on cold loads.
    """
    required=max(8192,needed,minimum)
    if required>MAX_CONTEXT:raise ValueError('Правила слишком длинные для контекста модели')
    return 8192 if required<=8192 else MAX_CONTEXT


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
    # Evidence must not be crowded out by editorial policies. Required style
    # stays in the payload; every original rule is still independently checked.
    for name, records in (('verified_facts', facts), ('owner_rules', rules)):
        for record in records:
            body[name].append(record)
            if request_tokens(messages(), predict=768) > limit - 1024:
                body[name].pop()
    return messages()
