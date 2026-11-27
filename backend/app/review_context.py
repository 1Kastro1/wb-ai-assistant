"""Bind approval to the complete buyer message and the exact outgoing answer."""
import hashlib
import json
import re

STALE_CONTEXT = 'Сначала проверьте ответ по полному тексту отзыва и актуальным правилам.'
STALE_FACTS = 'Сначала проверьте ответ по актуальным сведениям о товаре.'
CONTEXT_RULE = (
    'Сверь ответ со всем текстом покупателя, включая достоинства, недостатки и дополнения. '
    'Последнее уточнение определяет, осталась ли проблема. Ответь на главную претензию и существенные замечания, '
    'даже при 4–5 звёздах. Различай слишком сильный, слабый, неприятный и недолговечный аромат. '
    'Пересорт, повреждение, вскрытие и некомплект не объясняй вкусом покупателя, хранением или установкой. '
    'Учитывай этап получения заказа, предлагай применимый шаг без обещаний замены или компенсации. '
    'Не приписывай покупателю действия, причины или впечатления. Пиши естественно по-русски.'
)


def buyer_message(review):
    text = getattr(review, 'text', '') or ''
    sections = getattr(review, 'sections', None) or {}
    normalized = ' '.join(text.split())
    for field in ('advantages', 'disadvantages', 'comment', 'supplement'):
        value = sections.get(field, '')
        # Most imports already flatten these fields. Preserve their stored order.
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if ' '.join(value.split()) not in normalized:
            text = '\n'.join(filter(None, (text, value)))
            normalized = ' '.join(text.split())
    return text


def needs_context_check(review):
    if getattr(review, 'marketplace', '') not in ('wb', 'ozon', 'yandex') or not buyer_message(review).strip():
        return False
    from .equivalent_reviews import message_group
    from .rule_execution import simple_positive, UNTRIED_TEXTS
    if buyer_message(review) != (review.text or '') or (getattr(review, 'sections', None) or {}).get('supplement'):
        return True
    normalized = ' '.join(re.sub(r'[.!?,;…]', ' ', review.text.casefold().replace('ё', 'е')).split())
    # These groups enumerate equivalent *whole* messages. Their deterministic
    # publication check still re-evaluates the actual text and outgoing reply.
    return not simple_positive(review) and normalized not in UNTRIED_TEXTS and not message_group(review.text)


def review_details(message):
    """Spot recurring confusions; the model still checks the complete message."""
    value = ' '.join(message.casefold().replace('ё', 'е').split())
    details = set()
    if re.search(r'(?:заказ\w*[^.!?]{0,45}(?:запах|аромат)[^.!?]{0,25}[,.]?\s*'
                 r'(?:а\s+)?(?:пришел|прислали|получил\w*)\s+другой|'
                 r'(?:пришел|прислал\w*|получил\w*)\s+(?:другой|не тот)\s+(?:запах|аромат))', value):
        details.add('wrong_scent')
    if re.search(r'(?:перевернул\w*|переставил\w*|поменял\w* положение).{0,120}'
                 r'(?:аромат|запах).{0,30}стал\w*\s+(?:более\s+)?(?:явн(?:ый|ым)|заметнее|сильнее|ярче)', value):
        details.add('scent_improved')
    duration = re.search(r'раньше.{0,60}хвата\w*\s+(?:на\s+)?(\d+)\s*мес\w*.{0,35}'
                         r'сейчас.{0,25}?\b(\d+|один|два|три|четыре|пять|шесть)\b', value)
    numbers = {'один': 1, 'два': 2, 'три': 3, 'четыре': 4, 'пять': 5, 'шесть': 6}
    if duration and int(duration[1]) > (int(duration[2]) if duration[2].isdecimal() else numbers[duration[2]]):
        details.add('scent_duration')
    strength = re.sub(r'\bне\s+(?:слишком|чересчур)\s+\w+', '', value)
    if re.search(r'(?:запах|аромат)[^.!?]{0,85}(?:слишком\s+(?:много|сильн\w*|насыщенн\w*)|чересчур\s+сильн\w*)'
                 r'|(?:слишком|чересчур)\s+(?:сильн\w*|насыщенн\w*)\s+(?:запах|аромат)', strength):
        details.add('scent_too_strong')
    return details


def context_proof(review, answer):
    # No review identifier: an exact-context template can reuse the same proof.
    payload = [CONTEXT_RULE, review.marketplace, review.rating, buyer_message(review),
               getattr(review, 'sections', None) or {}, answer]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def context_current(review, answer, verdict):
    verdict = verdict or {}
    proof = verdict.get('review_context_proof')
    if not proof:
        return not needs_context_check(review)
    return (verdict.get('checked') is True and verdict.get('passed') is True
            and proof == context_proof(review, answer))


def facts_current(db, review, draft, product, rules, *, input_key=None):
    """For non-template replies check the exact input verified by the worker."""
    if not needs_context_check(review):
        return True
    proof = (draft.quality or {}).get('owner_rules') or {}
    if not proof.get('input_key'):
        return False
    if input_key is None:
        from .rule_execution import verification_key
        from .services import review_verified_facts
        input_key = verification_key(draft.text, review, product, rules, review_verified_facts(db, review, product, rules))
    return proof['input_key'] == input_key
