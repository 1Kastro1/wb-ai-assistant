import re
from pathlib import Path


COMPLAINT_PATTERNS = {
    'WRONG_ITEM': ('не тот товар', 'другой товар', 'пришел не тот', 'прислали не тот'),
    'MISSING_PARTS': ('не хватает', 'нет детал', 'неполная комплектац', 'не доложили', 'недостает'),
    'DAMAGED_ITEM': ('сломан', 'разбит', 'товар поврежд', 'товар помят', 'трещин'),
    'QUALITY_ISSUE': ('плохое качество', 'не работает', 'неисправ', 'дефект', 'слабая искра', 'криво бьет', 'быстро выш'),
    'PACKAGING_ISSUE': ('упаковка поврежд', 'упаковка помят', 'коробка помят', 'плохо упакован', 'упаковка вскрыт'),
    'DESCRIPTION_MISMATCH': ('не соответствует описанию', 'не как на фото', 'отличается от описания', 'характеристики не совпадают'),
    'FITMENT_PROBLEM': ('не подошел', 'не подходит', 'несовместим'),
    'NO_RESULT': ('нет результата', 'не помог', 'не очищает', 'не очистил', 'не отмыл', 'бесполезн', 'деньги на ветер', 'толку нет'),
    'ODOR_COMPLAINT': ('воняет', 'вонюч', 'неприятный запах', 'резкий запах', 'ужасный запах', 'запах не понрав', 'аромат не понрав', 'не понравился запах', 'не понравился аромат'),
    'WEAK_SCENT': ('запаха почти нет', 'слабый запах', 'слабый аромат', 'не пахнет', 'аромат не чувств', 'запах не чувств', 'почти не пах', 'еле пах', 'нет запаха'),
    'DELIVERY_ISSUE': ('долго достав', 'задержали достав', 'проблема с достав', 'не достав'),
}
TOPIC_WORDS = {
    'WRONG_ITEM': ('не тот', 'другой товар', 'выбранный товар'),
    'MISSING_PARTS': ('комплект', 'недоста', 'не хватает', 'элемент', 'детал'),
    'DAMAGED_ITEM': ('поврежд', 'разбит', 'сломан', 'трещин'),
    'QUALITY_ISSUE': ('качеств', 'неисправ', 'дефект', 'искр', 'работ'),
    'PACKAGING_ISSUE': ('упаков', 'коробк'),
    'DESCRIPTION_MISMATCH': ('описан', 'характеристик', 'производител', 'маркиров'),
    'FITMENT_PROBLEM': ('совместим', 'подбор', 'подош', 'подход'),
    'NO_RESULT': ('результат', 'эффект', 'очищ', 'применен', 'инструкц'),
    'ODOR_COMPLAINT': ('запах', 'аромат'),
    'WEAK_SCENT': ('запах', 'аромат'),
    'DELIVERY_ISSUE': ('достав', 'получен', 'заказ'),
}


def analyze_customer_message(text, rating=None):
    """Conservative internal hints; the complete buyer text remains model input."""
    value = ' '.join((text or '').lower().replace('ё', 'е').split())
    def matches(marker):
        return bool(re.search(r'(?<!\w)' + re.escape(marker), value)) if marker.startswith('не ') else marker in value
    complaints = [kind for kind, markers in COMPLAINT_PATTERNS.items() if any(matches(x) for x in markers)]
    if any(x in value for x in ('производител', 'япони', 'китай', 'страна', 'оригинал')) and any(x in value for x in ('х/з', 'хз', 'непонят', 'неизвест', 'без маркиров')) and any(x in value for x in ('приш', 'получ', 'присла')):
        if 'DESCRIPTION_MISMATCH' not in complaints:
            complaints.append('DESCRIPTION_MISMATCH')
    positive = any(not re.search(r'(?<!\w)не\s*$', value[:match.start()]) for match in re.finditer(r'\b(?:понравил\w*|отличн\w*|рекомендую|хорош\w*)', value))
    negative = bool(complaints) or (rating is not None and rating <= 3) or any(x in value for x in ('разочар', 'недовол')) or bool(re.search(r'(?<!\w)не\s+понрав', value))
    question = '?' in value or bool(re.search(r'\b(?:как|сколько|какой|какая|подойдет|можно ли|где|когда|подскажите)\b', value))
    financial = bool(re.search(r'(?:верн|возврат)\w*[^.!?]{0,35}(?:деньг|оплат|стоим|средств)|компенсац|возмещ\w*\s+(?:расход|стоим)', value)) or ('деньг' in value and any(x in value for x in ('возврат', 'выплат', 'верн')))
    return {
        'sentiment': 'mixed' if positive and negative else 'negative' if negative else 'positive' if positive or (rating is not None and rating >= 4) else 'unknown',
        'complaints': complaints,
        'primary_complaint': complaints[0] if complaints else None,
        'is_question': question,
        'requested_information': text.strip() if question else '',
        'buyer_asks_about_money': financial,
        'cause_confirmed': False,
    }


def missing_complaints(analysis, reply):
    value = reply.lower().replace('ё', 'е')
    return [kind for kind in analysis['complaints'] if not any(x in value for x in TOPIC_WORDS[kind])]


def customer_reply_policy():
    return (Path(__file__).parent / 'prompts/customer_reply_policy.md').read_text(encoding='utf-8')


def official_support_reply():
    return 'Здравствуйте! Для рассмотрения Вашего обращения обратитесь, пожалуйста, в официальную поддержку маркетплейса. Укажите номер заказа и опишите ситуацию, чтобы обращение можно было проверить.'


def contextual_complaint_reply(analysis, revision=0):
    clauses = {
        'WRONG_ITEM': 'Вы получили не тот товар',
        'MISSING_PARTS': 'в комплекте не хватает элементов',
        'DAMAGED_ITEM': 'товар оказался повреждённым',
        'QUALITY_ISSUE': 'качество или работа товара Вас разочаровали',
        'PACKAGING_ISSUE': 'упаковка пришла в ненадлежащем состоянии',
        'DESCRIPTION_MISMATCH': 'полученный товар отличается от описания',
        'FITMENT_PROBLEM': 'товар не подошёл',
        'NO_RESULT': 'применение не дало ожидаемого результата',
        'ODOR_COMPLAINT': 'запах оказался неприятным',
        'WEAK_SCENT': 'аромат оказался слабее ожидаемого',
        'DELIVERY_ISSUE': 'при доставке заказа возникли затруднения',
    }
    details = [clauses[kind] for kind in analysis['complaints']]
    if not details:
        return None
    thanks = ('Спасибо за обратную связь.', 'Благодарим, что рассказали о ситуации.', 'Спасибо за Ваш отзыв.')[revision % 3]
    ending = 'Для дополнительной проверки можно обратиться в официальную поддержку маркетплейса.' if analysis['buyer_asks_about_money'] else 'По отзыву нельзя достоверно установить причину; без проверки не хотим вводить Вас в заблуждение.'
    return 'Здравствуйте! ' + thanks + ' Сожалеем, что ' + '; '.join(details) + '. ' + ending
