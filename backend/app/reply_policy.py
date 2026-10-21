import re
from pathlib import Path


def strip_other_product_comparisons(text):
    """Ignore failures attributed to other cleaners, retaining this item's complaints."""
    effect = r'(?:помог\w*|очист\w*|отмы\w*|справ\w*)'
    other = r'друг(?:ие|ое|ой|ая)(?:\s+(?:средств\w*|очистител\w*|состав\w*|хими\w*|продукт\w*))?'
    text = re.sub(r'(?<!\w)не\s+' + effect + r'[^,.!?;\n]{0,60}\b' + other + r'(?=\s*[,.:!?;\n]|$)', ' ', text, flags=re.I)
    text = re.sub(r'\bдруг(?:ие|ое|ой|ая)\s+(?:средств\w*|очистител\w*|состав\w*|хими\w*)\s+не\s+' + effect, ' ', text, flags=re.I)
    return text


def weak_cleaning_result(text):
    value = ' '.join((text or '').lower().replace('ё', 'е').split())
    for match in re.finditer(r'(?<!\w)(?:слабо|плохо|неважно|почти не|не)\s+(?:смыва\w*|отмыва\w*|очища\w*|удаля\w*|справля\w*)', value):
        if not re.search(r'(?<!\w)не\s*$', value[:match.start()]): return True
    return False


def authenticity_concern(text):
    value = ' '.join((text or '').lower().replace('ё', 'е').split())
    if re.search(r'(?<!\w)не\s*оригинал|контрафакт|фальсификат|пален', value): return True
    if any(not re.search(r'(?<!\w)не\s*$', value[:match.start()]) for match in re.finditer(r'(?:жалк\w*\s+)?пародия\s+на\b', value)):
        return True
    return any(not re.search(r'(?<!\w)(?:не|нет)\s*$', value[:match.start()])
               for match in re.finditer(r'поддел\w*', value))


def faded_scent(text):
    value = ' '.join((text or '').lower().replace('ё', 'е').split())
    pattern = r'перестал[аио]?\s+пахн\w*|(?:запах|аромат)\w*[^.!?;]{0,35}?(?:исчез|пропал|выветрил)\w*|(?:исчез|пропал|выветрил)\w*[^.!?;]{0,35}?(?:запах|аромат)\w*'
    for match in re.finditer(pattern, value):
        context = value[max(0,match.start()-25):match.end()]
        if re.search(r'(?<!\w)не\s+(?:перестал|исчез|пропал|выветрил)', context):continue
        if re.search(r'неприятн|посторонн|плохой запах|запах (?:табака|сырости|пота)', context):continue
        return True
    return False



def yellow_light_complaint(text):
    value = ' '.join((text or '').lower().replace('ё','е').split())
    return bool(re.search(r'свет[^.!?]{0,35}(?:желт|с\s*желта)|(?:желт\w*|с\s*желта)[^.!?]{0,20}свет|\bжелтит\b',value))


def subtle_scent_complaint(text):
    value = ' '.join((text or '').lower().replace('ё','е').split())
    if re.search(r'(?:пахн\w*|(?:запах|аромат)\w*)[^.!?;]{0,20}кое[\s-]*как', value):
        return True
    return bool(re.search(r'(?:пахн\w*|(?:запах|аромат)\w*)\s*(?:совсем\s+|очень\s+|лишь\s+|только\s+)*(?:чуть[\s-]*чуть|еле|едва|слабоват\w*)|слабоват\w*\s+(?:запах|аромат)', value))

COMPLAINT_PATTERNS = {
    'WRONG_ITEM': ('не тот товар', 'другой товар', 'пришел не тот', 'прислали не тот'),
    'MISSING_PARTS': ('не хватает', 'нет детал', 'неполная комплектац', 'не доложили', 'недостает'),
    'DAMAGED_ITEM': ('сломал', 'сломан', 'разбит', 'товар поврежд', 'товар помят', 'трещин'),
    'QUALITY_ISSUE': ('плохое качество', 'не работает', 'неисправ', 'дефект', 'слабая искра', 'криво бьет', 'быстро выш'),
    'PACKAGING_ISSUE': ('упаковка поврежд', 'упаковка помят', 'коробка помят', 'плохо упакован', 'упаковали плохо', 'упаковка вскрыт', 'крышка без заводской защиты', 'одета криво'),
    'LEAKAGE': ('протек', 'потек', 'протеч'),
    'RESIDUE': ('белые разводы', 'жирный слой', 'вазелиновое состояние'),
    'DESCRIPTION_MISMATCH': ('не соответствует описанию', 'не как на фото', 'отличается от описания', 'характеристики не совпадают'),
    'FITMENT_PROBLEM': ('не подошел', 'не подходит', 'несовместим'),
    'NO_RESULT': ('нет результата', 'не увидела результата', 'не увидел результата', 'не помог', 'не очищает', 'не очистил', 'не отмыл', 'бесполезн', 'деньги на ветер', 'толку нет'),
    'ODOR_COMPLAINT': ('воняет', 'вонюч', 'неприятный запах', 'резкий запах', 'ужасный запах', 'невыносимый запах', 'запах кислый', 'запах не понрав', 'аромат не понрав', 'не понравился запах', 'не понравился аромат'),
    'WEAK_SCENT': ('запаха почти нет', 'слабый запах', 'слабый аромат', 'слабо пахнет', 'пахнет слабо', 'не пахнет', 'аромат не чувств', 'запах не чувств', 'почти не пах', 'еле пах', 'нет запаха'),
    'DIM_LIGHT': ('свет тусклый', 'тусклый свет', 'слабо светит', 'свет слабый'),
    'DELIVERY_ISSUE': ('долго достав', 'задержали достав', 'проблема с достав', 'не достав'),
}
TOPIC_WORDS = {
    'AUTHENTICITY': ('оригинал', 'подлин', 'дистрибьютор', 'поддел'),
    'WRONG_ITEM': ('не тот', 'другой товар', 'выбранный товар'),
    'MISSING_PARTS': ('комплект', 'недоста', 'не хватает', 'элемент', 'детал'),
    'DAMAGED_ITEM': ('поврежд', 'разбит', 'сломан', 'сломал', 'поломк', 'трещин'),
    'QUALITY_ISSUE': ('качеств', 'неисправ', 'дефект', 'искр', 'работ'),
    'PACKAGING_ISSUE': ('упаков', 'коробк'),
    'LEAKAGE': ('протеч', 'протек', 'потек'),
    'RESIDUE': ('развод', 'жирный слой'),
    'DESCRIPTION_MISMATCH': ('описан', 'характеристик', 'производител', 'маркиров'),
    'FITMENT_PROBLEM': ('совместим', 'подбор', 'подош', 'подход'),
    'NO_RESULT': ('результат', 'эффект', 'очищ', 'применен', 'инструкц'),
    'ODOR_COMPLAINT': ('запах', 'аромат'),
    'WEAK_SCENT': ('запах', 'аромат'),
    'DIM_LIGHT': ('свет', 'ламп', 'ярк', 'напряж'),
    'DELIVERY_ISSUE': ('достав', 'получен', 'заказ'),
}


def analyze_customer_message(text, rating=None):
    """Conservative internal hints; the complete buyer text remains model input."""
    value = ' '.join(strip_other_product_comparisons(text or '').lower().replace('ё', 'е').split())
    def matches(marker):
        if marker in ('сломал','сломан','протек','потек','протеч'):
            return any(not re.search(r'(?<!\w)(?:не|без)\s*$', value[:match.start()]) for match in re.finditer(r'(?<!\w)' + re.escape(marker), value))
        return bool(re.search(r'(?<!\w)' + re.escape(marker), value)) if marker.startswith('не ') else marker in value
    complaints = [kind for kind, markers in COMPLAINT_PATTERNS.items() if any(matches(x) for x in markers)]
    if yellow_light_complaint(text) and 'DIM_LIGHT' not in complaints:complaints.append('DIM_LIGHT')
    if (faded_scent(text) or subtle_scent_complaint(text)) and 'WEAK_SCENT' not in complaints:complaints.append('WEAK_SCENT')
    if any(x in value for x in ('производител', 'япони', 'китай', 'страна', 'оригинал')) and any(x in value for x in ('х/з', 'хз', 'непонят', 'неизвест', 'без маркиров')) and any(x in value for x in ('приш', 'получ', 'присла')):
        if 'DESCRIPTION_MISMATCH' not in complaints:
            complaints.append('DESCRIPTION_MISMATCH')
    if weak_cleaning_result(text) and 'NO_RESULT' not in complaints: complaints.append('NO_RESULT')
    if authenticity_concern(text) and 'AUTHENTICITY' not in complaints: complaints.append('AUTHENTICITY')
    positive = any(not re.search(r'(?<!\w)не\s*$', value[:match.start()]) for match in re.finditer(r'\b(?:понравил\w*|отличн\w*|рекомендую|хорош\w*|вкусн\w*|приятн\w*|ненавязчив\w*)', value))
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
        'AUTHENTICITY': 'подлинность товара вызвала сомнения',
        'WRONG_ITEM': 'Вы получили не тот товар',
        'MISSING_PARTS': 'в комплекте не хватает элементов',
        'DAMAGED_ITEM': 'товар оказался повреждённым',
        'QUALITY_ISSUE': 'качество или работа товара Вас разочаровали',
        'PACKAGING_ISSUE': 'упаковка пришла в ненадлежащем состоянии',
        'LEAKAGE': 'средство протекло',
        'RESIDUE': 'после применения остались разводы',
        'DESCRIPTION_MISMATCH': 'полученный товар отличается от описания',
        'FITMENT_PROBLEM': 'товар не подошёл',
        'NO_RESULT': 'применение не дало ожидаемого результата',
        'ODOR_COMPLAINT': 'запах оказался неприятным',
        'WEAK_SCENT': 'аромат оказался слабее ожидаемого',
        'DIM_LIGHT': 'свет оказался тусклым',
        'DELIVERY_ISSUE': 'при доставке заказа возникли затруднения',
    }
    details = [clauses[kind] for kind in analysis['complaints']]
    if not details:
        return None
    thanks = ('Спасибо за обратную связь.', 'Благодарим, что рассказали о ситуации.', 'Спасибо за Ваш отзыв.')[revision % 3]
    ending = 'Для дополнительной проверки можно обратиться в официальную поддержку маркетплейса.' if analysis['buyer_asks_about_money'] else 'Для проверки уточните, пожалуйста, детали проблемы; при необходимости обратитесь в официальную поддержку маркетплейса.'
    return 'Здравствуйте! ' + thanks + ' Приносим извинения за то, что ' + '; '.join(details) + '. ' + ending
