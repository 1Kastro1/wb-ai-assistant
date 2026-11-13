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
        if request_tokens(messages, schema) <= MAX_CONTEXT:
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
