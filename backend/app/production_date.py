"""A product model and certification dates do not identify a production lot."""
from datetime import date
import re


REQUEST = re.compile(r'(?:дат[ауеы]|месяц\w*|год\w*)[^?.!]{0,45}(?:выпуска|производства|изготовления)|когда\s+(?:выпущен|произведен|изготовлен)', re.I)
DATE_TOKEN = re.compile(r'\b(?:\d{1,4}(?:[./-]\d{1,4}){1,2}|\d{2,4}|дв[еу]\s+тысяч\w*|январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяе]|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\b', re.I)
ISSUE = 'Указывать месяц и год выпуска только по подтверждённым сведениям об этой партии или экземпляре.'


def requested(question):
    return (getattr(question, 'marketplace', '').endswith('_question')
            and bool(REQUEST.search((question.text or '').casefold().replace('ё', 'е'))))


def single_request(question):
    if not requested(question):
        return False
    text = question.text.strip().rstrip('?.!').casefold()
    match = REQUEST.search(text.replace('ё', 'е'))
    return (not re.search(r'[?;]|\b(?:гарантия|подойдет|подойдёт|совместим|комплект|сколько стоит)\w*\b', text)
            and not re.search(r'\bи\b', text[match.end():]))


def confirmed_month(question, facts):
    months = set()
    for fact in facts or ():
        # An accepted fact must explicitly identify the inquiry about that lot
        # or item. A product-level or certificate date alone is insufficient.
        if (fact.get('kind') != 'production_date' or fact.get('review_id') != question.id
                or fact.get('verification_status') != 'VERIFIED'):
            continue
        value = fact.get('date')
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?', value):
            continue
        try:
            parsed = date.fromisoformat(value if len(value) == 10 else value + '-01')
        except ValueError:
            continue
        months.add(parsed.strftime('%m.%Y'))
    return next(iter(months)) if len(months) == 1 else ''


def claim_issue(question, answer, facts):
    if not requested(question):
        return ''
    date_parts = DATE_TOKEN.findall(answer)
    return ISSUE if date_parts and set(date_parts) != {confirmed_month(question, facts)} else ''


def publication_issue(db, question, product, reply):
    if not requested(question):
        return ''
    from .services import trusted_product_facts
    return claim_issue(question, reply, trusted_product_facts(db, product))


def answer(question, product, facts):
    month = confirmed_month(question, facts)
    subject = 'аккумулятора' if 'аккумулятор' in product.name.casefold() else 'товара'
    battery_model = re.search(r'\b\d{2,3}[bб]\d{2}[rl]\b', question.text, re.I)
    if subject == 'аккумулятора' and battery_model:
        subject += ' ' + battery_model[0].upper().replace('Б', 'B')
    if month:
        return f'Здравствуйте! Спасибо за вопрос. Подтверждённый месяц и год выпуска {subject} — {month}.'
    return (f'Здравствуйте! Спасибо за вопрос. По данным карточки не можем подтвердить '
            f'точный месяц и год выпуска {subject}. Для этого нужны сведения о конкретной партии или экземпляре.')
