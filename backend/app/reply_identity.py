"""Keep another buyer's identity out of a generated reply's opening."""
import re


_GREETING = r'(?:здравствуйте|добрый\s+(?:день|вечер)|доброе\s+утро|приветствуем(?:\s+Вас)?)'
_NAME = r'[А-ЯЁA-Z][а-яёa-z-]+(?:\s+[А-ЯЁA-Z][а-яёa-z-]+){0,2}'
_NOT_NAMES = frozenset(('спасибо', 'благодарим', 'благодарю', 'рады', 'желаем',
    'пусть', 'приносим', 'сожалеем', 'извините', 'простите', 'если', 'при',
    'для', 'мы', 'нам', 'вы', 'ваш', 'ваша', 'ваши', 'всегда', 'пожалуйста'))


def confirmed_buyer_name(review):
    value = str(getattr(review, 'buyer_name', '') or getattr(review, 'customer_name', '') or '').strip()
    if (not value or getattr(review, 'buyer_name_hidden', False) or
            re.search(r'скрыт|аноним|пользователь|покупатель|hidden|anonymous|\*', value, re.I)):
        return ''
    return value if re.fullmatch(_NAME, value) else ''


def normalize_reply_opening(reply, review):
    """Remove an unsupported salutation only, preserving body and known names.

    This is formatting, not proof that the rest of a reply is factual. Library
    examples still need their exact buyer/product/facts/rules context to match.
    """
    known = confirmed_buyer_name(review).casefold()
    before = re.match(r'^\s*(?P<name>[^\n.!?,]{1,80}),\s*(?=' + _GREETING + r'\b)', reply, re.I)
    if before and before['name'].strip().casefold() != known:
        reply = reply[before.end():]
    # Cover both "Здравствуйте, Мария!" and "Здравствуйте. Мария, спасибо".
    after = re.match(r'^(?P<opening>\s*(?i:' + _GREETING + r')[!.,:]\s*)'
                     r'(?P<name>' + _NAME + r')[!,:]\s*', reply)
    if after and after['name'].casefold() not in _NOT_NAMES and after['name'].casefold() != known:
        opening = after['opening'].rstrip()
        if opening.endswith(','):
            opening = opening[:-1] + '.'
        reply = opening + ' ' + reply[after.end():]
    return reply
