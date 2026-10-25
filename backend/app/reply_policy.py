import re
from pathlib import Path


def complaint_text(text):
    """Remove explicit denials of faults, not the buyer's genuine complaints."""
    value = (text or '').lower().replace('ё', 'е')
    value = re.sub(r'(?:ни разу[^.!?\n]{0,35}не|не)\s+(?:попал\w*|встретил\w*|заметил\w*|обнаружил\w*)[^.!?\n]{0,20}неприятн\w*\s+(?:запах|аромат)\w*', ' приятный аромат ', value)
    value = re.sub(r'(?:нет|без|не было)\s+(?:никакого\s+)?(?:неприятн\w*|резк\w*|посторонн\w*)\s+(?:запах|аромат)\w*', ' ', value)
    value = re.sub(r'(?:неприятн\w*|резк\w*|посторонн\w*)\s+(?:запах|аромат)\w*\s+нет', ' ', value)
    value = re.sub(r'(?<!\w)(?:не|без)\s+(?:скрип\w*|шум\w*|воня\w*|помят\w*|поврежд\w*)', ' ', value)
    return value


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
    if any(not re.search(r'(?<!\w)не\s*$', value[:match.start()]) for match in re.finditer(r'(?:жалк\w*\s+)?п[ао]родия\s+на\b', value)):
        return True
    return any(not re.search(r'(?<!\w)(?:не|нет)\s*$', value[:match.start()])
               for match in re.finditer(r'поддел\w*', value))


def faded_scent(text):
    value = ' '.join((text or '').lower().replace('ё', 'е').split())
    pattern = r'перестал[аио]?\s+пахн\w*|(?:запах|аромат)\w*[^.!?;]{0,35}?(?:исчез|пропал|выветри[лв])\w*|(?:исчез|пропал|выветри[лв])\w*[^.!?;]{0,35}?(?:запах|аромат)\w*'
    for match in re.finditer(pattern, value):
        context = value[max(0,match.start()-25):match.end()]
        if re.search(r'(?<!\w)не\s+(?:перестал|исчез|пропал|выветрил)', context):continue
        if re.search(r'неприятн|посторонн|хими|плохой запах|запах (?:табака|сырости|пота)', context):continue
        return True
    return False



def yellow_light_complaint(text):
    value = ' '.join((text or '').lower().replace('ё','е').split())
    if re.search(r'светят?\s+(?:хорошо|отлично)|хорош\w*\s+ламп', value) and not re.search(r'тускл|слишком|не понрав|ожидал|не соответствует|вместо|не бел|желтит', value):
        return False
    return bool(re.search(r'свет[^.!?]{0,35}(?:желт|с\s*желта)|(?:желт\w*|с\s*желта)[^.!?]{0,20}свет|\bжелтит\b',value))


def subtle_scent_complaint(text):
    value = ' '.join((text or '').lower().replace('ё','е').split())
    if re.search(r'(?:пахн\w*|(?:запах|аромат)\w*)[^.!?;]{0,20}кое[\s-]*как', value):
        return True
    return bool(re.search(r'(?:пахн\w*|(?:запах|аромат)\w*)\s*(?:совсем\s+|очень\s+|лишь\s+|только\s+)*(?:чуть[\s-]*чуть|еле|едва|слабоват\w*)|слабоват\w*\s+(?:запах|аромат)', value))


def quantity_shortage(text):
    value = ' '.join((text or '').lower().replace('ё', 'е').split())
    return bool(re.search(r'вместо\s+(?:двух|2|трех|3|четырех|4)[^.!?]{0,35}(?:один|одну|1)|(?:пришел|пришла|прислали|отправили|получил\w*)[^.!?]{0,20}(?:один|одну|1)[^.!?]{0,20}(?:вместо|а не)\s+(?:двух|два|две|2)', value))


def buyer_question(text):
    # Comparisons and rhetorical praise are not requests for instructions.
    value = (text or '').lower().replace('ё', 'е')
    value = re.sub(r'вроде как|как в описании|как родн\w*|не как все|какие могут быть недостатки[^.!?]*[?]?', ' ', value)
    return '?' in value or bool(re.search(r'подскажите|можно ли|нужно ли|почему|зачем|как\s+(?:правильно\s+)?(?:примен|использ|установ|нанос|развод|закреп|подключ|выбра)', value))


def favorable_price(text):
    value = (text or '').lower().replace('ё', 'е')
    if re.search(r'дорог|завыш|цена\s+(?:высок|куса)|стоимост\w*\s+высок', value):
        return False
    return bool(re.search(r'недорог|дешев|цен[аыуе][^.!?\n]{0,20}(?:низк|хорош|устра|отлич|подарок)|за такую (?:цену|стоимость)[^.!?\n]{0,30}(?:хорош|супер|отлич)|цена\s*[-—/]?\s*качеств', value))

COMPLAINT_PATTERNS = {
    'WRONG_ITEM': ('не тот товар', 'другой товар', 'пришел не тот', 'прислали не тот'),
    'MISSING_PARTS': ('не хватает', 'нет детал', 'неполная комплектац', 'не доложили', 'недостает'),
    'DAMAGED_ITEM': ('сломал', 'сломан', 'разбит', 'товар поврежд', 'товар помят', 'трещин'),
    'QUALITY_ISSUE': ('плохое качество', 'не работает', 'нерабоч', 'неисправ', 'дефект', 'слабая искра', 'криво бьет', 'быстро выш'),
    'PACKAGING_ISSUE': ('упаковка поврежд', 'упаковка помят', 'коробка помят', 'плохо упакован', 'упаковали плохо', 'упаковка вскрыт', 'крышка без заводской защиты', 'одета криво'),
    'LEAKAGE': ('протек', 'потек', 'протеч', 'вытек', 'утек'),
    'RESIDUE': ('белые разводы', 'жирный слой', 'вазелиновое состояние'),
    'DESCRIPTION_MISMATCH': ('не соответствует описанию', 'не как на фото', 'отличается от описания', 'характеристики не совпадают'),
    'FITMENT_PROBLEM': ('не подошел', 'не подходит', 'несовместим'),
    'NO_RESULT': ('нет результата', 'не увидела результата', 'не увидел результата', 'не помог', 'не очищает', 'не очистил', 'не отмыл', 'бесполезн', 'деньги на ветер', 'толку нет'),
    'ODOR_COMPLAINT': ('воняет', 'вонюч', 'неприятный запах', 'запах неприят', 'аромат неприят', 'резкий запах', 'ужасный запах', 'невыносимый запах', 'запах кислый', 'запах не понрав', 'аромат не понрав', 'не понравился запах', 'не понравился аромат'),
    'WEAK_SCENT': ('запаха почти нет', 'слабый запах', 'слабый аромат', 'слабо пахнет', 'пахнет слабо', 'не пахнет', 'аромат не чувств', 'запах не чувств', 'почти не пах', 'еле пах', 'нет запаха'),
    'DIM_LIGHT': ('свет тусклый', 'тусклый свет', 'слабо светит', 'свет слабый'),
    'DELIVERY_ISSUE': ('долго достав', 'задержали достав', 'проблема с достав', 'не достав'),
    'SIZE_ISSUE': ('слишком коротк', 'короткая', 'короткий'),
}
TOPIC_WORDS = {
    'AUTHENTICITY': ('оригинал', 'подлин', 'дистрибьютор', 'поддел'),
    'WRONG_ITEM': ('не тот', 'другой товар', 'выбранный товар'),
    'MISSING_PARTS': ('комплект', 'недоста', 'не хватает', 'элемент', 'детал'),
    'DAMAGED_ITEM': ('поврежд', 'разбит', 'сломан', 'сломал', 'поломк', 'трещин'),
    'QUALITY_ISSUE': ('качеств', 'неисправ', 'дефект', 'искр', 'работ', 'поломк', 'шум', 'скрип', 'щетк'),
    'PACKAGING_ISSUE': ('упаков', 'коробк'),
    'LEAKAGE': ('протеч', 'протек', 'потек'),
    'RESIDUE': ('развод', 'жирный слой'),
    'DESCRIPTION_MISMATCH': ('описан', 'характеристик', 'производител', 'маркиров'),
    'FITMENT_PROBLEM': ('совместим', 'подбор', 'подош', 'подход'),
    'NO_RESULT': ('результат', 'эффект', 'очищ', 'очист', 'применен', 'инструкц'),
    'ODOR_COMPLAINT': ('запах', 'аромат'),
    'WEAK_SCENT': ('запах', 'аромат'),
    'DIM_LIGHT': ('свет', 'ламп', 'ярк', 'напряж'),
    'DELIVERY_ISSUE': ('достав', 'получен', 'заказ'),
    'SIZE_ISSUE': ('длин', 'корот', 'размер'),
}

COMPLAINT_LABELS = {
    'AUTHENTICITY':'сомнения в оригинальности', 'WRONG_ITEM':'пришёл другой товар',
    'MISSING_PARTS':'недостача или комплектация', 'DAMAGED_ITEM':'повреждение товара',
    'QUALITY_ISSUE':'неисправность или качество', 'PACKAGING_ISSUE':'состояние упаковки',
    'LEAKAGE':'протечка', 'RESIDUE':'следы и разводы', 'DESCRIPTION_MISMATCH':'несоответствие описанию',
    'FITMENT_PROBLEM':'деталь не подошла', 'NO_RESULT':'нет результата',
    'ODOR_COMPLAINT':'неприятный запах', 'WEAK_SCENT':'слабый аромат',
    'DIM_LIGHT':'тусклый свет', 'DELIVERY_ISSUE':'проблема с доставкой',
    'SIZE_ISSUE':'размер товара',
}


def analyze_customer_message(text, rating=None):
    """Conservative internal hints; the complete buyer text remains model input."""
    value = ' '.join(complaint_text(strip_other_product_comparisons(text or '')).split())
    def matches(marker):
        if marker in ('сломал','сломан','протек','потек','протеч','вытек','утек'):
            return any(not re.search(r'(?<!\w)(?:не|без)\s*$', value[:match.start()]) for match in re.finditer(r'(?<!\w)' + re.escape(marker), value))
        return bool(re.search(r'(?<!\w)' + re.escape(marker), value)) if marker.startswith('не ') else marker in value
    complaints = [kind for kind, markers in COMPLAINT_PATTERNS.items() if any(matches(x) for x in markers)]
    extra = {
        'PACKAGING_ISSUE': r'(?:короб\w*|упаков\w*)\s+(?:\w+\s+){0,2}(?:помят|мят|поврежд)|(?:помят|мят)\w*\s+(?:короб|упаков)',
        'WRONG_ITEM': r'прислал\w*\s+(?:две\s+)?разн\w*\s+щетк',
        'NO_RESULT': r'слабоват|плохо чист|через день[^.!?]{0,20}мутн',
        'DIM_LIGHT': r'тусклее|не светят',
        'QUALITY_ISSUE': r'скрип|издают много|нету? захода[^.!?]{0,15}резьб|надписи[^.!?]{0,35}крив',
        'DELIVERY_ISSUE': r'шел долго|шла долго|шло долго',
        'FITMENT_PROBLEM': r'не\s+подош(?:ло|ла|ли|ел)|не\s+подходит',
        'MISSING_PARTS': r'нет\s+уплотнител',
        'ODOR_COMPLAINT': r'(?:аромат|запах)[^.!?\n]{0,25}мне\s+не\s+(?:особо|зашел|нрав)|мне\s+не\s+зашел[^.!?\n]{0,25}(?:аромат|запах)|(?:аромат|запах)[^.!?\n]{0,25}не\s+понрав',
    }
    for kind, pattern in extra.items():
        if re.search(r'(?<!\w)(?:' + pattern + ')', value) and kind not in complaints:
            complaints.append(kind)
    if re.search(r'ломанн\w*\s+мелк|пришло в ужас\w*\s+состояни', value) and 'DAMAGED_ITEM' not in complaints:
        complaints.append('DAMAGED_ITEM')
    if re.search(r'химозн|душноват', value) and 'ODOR_COMPLAINT' not in complaints:
        complaints.append('ODOR_COMPLAINT')
    if re.search(r'отсутствует[^.!?]{0,25}запах|запах ощущается только', value) and 'WEAK_SCENT' not in complaints:
        complaints.append('WEAK_SCENT')
    if re.search(r'упакован\w*\s+(?:отвратительн\w*|плохо|ужасн\w*)|дран\w*\s+пакет', value) and 'PACKAGING_ISSUE' not in complaints:
        complaints.append('PACKAGING_ISSUE')
    if quantity_shortage(text) and 'MISSING_PARTS' not in complaints:
        complaints.append('MISSING_PARTS')
    if yellow_light_complaint(text) and 'DIM_LIGHT' not in complaints:complaints.append('DIM_LIGHT')
    if (faded_scent(text) or subtle_scent_complaint(text)) and 'WEAK_SCENT' not in complaints:complaints.append('WEAK_SCENT')
    if any(x in value for x in ('производител', 'япони', 'китай', 'страна', 'оригинал')) and any(x in value for x in ('х/з', 'хз', 'непонят', 'неизвест', 'без маркиров')) and any(x in value for x in ('приш', 'получ', 'присла')):
        if 'DESCRIPTION_MISMATCH' not in complaints:
            complaints.append('DESCRIPTION_MISMATCH')
    if weak_cleaning_result(text) and 'NO_RESULT' not in complaints: complaints.append('NO_RESULT')
    if authenticity_concern(text) and 'AUTHENTICITY' not in complaints: complaints.append('AUTHENTICITY')
    positive = any(not re.search(r'(?<!\w)не\s*$', value[:match.start()]) for match in re.finditer(r'\b(?:понравил\w*|отличн\w*|рекомендую|хорош\w*|вкусн\w*|приятн\w*|ненавязчив\w*)', value))
    negative = bool(complaints) or (rating is not None and rating <= 3) or any(x in value for x in ('разочар', 'недовол')) or bool(re.search(r'(?<!\w)не\s+понрав', value))
    question = buyer_question(text)
    financial = bool(re.search(r'(?:верн|возврат)\w*[^.!?]{0,35}(?:деньг|оплат|стоим|средств)|компенсац|возмещ\w*\s+(?:расход|стоим)', value)) or ('деньг' in value and any(x in value for x in ('возврат', 'выплат', 'верн')))
    return {
        'sentiment': 'mixed' if positive and negative else 'negative' if negative else 'positive' if positive or (rating is not None and rating >= 4) else 'unknown',
        'complaints': complaints,
        'primary_complaint': complaints[0] if complaints else None,
        'is_question': question,
        'requested_information': text.strip() if question else '',
        'buyer_asks_about_money': financial,
        'cause_confirmed': False,
        'sprayer_replaced': bool(re.search(r'(?:поменял\w*|заменил\w*)\s+(?:(?:его|распылитель)\s+)?на\s+нов\w*|(?:поменял\w*|заменил\w*)\s+распылитель', value)) and 'распылител' in value,
        'resolved': bool(re.search(r'(?:поменял\w*|заменил\w*)[^.!?]{0,30}(?:распылител|на нов)|перезаказал|одобрил\w*\s+возврат|проблем\w*\s+реш', value)),
        'buyer_text': text,
    }


# Broad requests to retell a known complaint are different from a targeted
# request for a missing VIN, surface, batch/photo or installation detail.
REDUNDANT_CLARIFICATION = re.compile(
    r'[^.!?\n]*(?:уточните|расскажите|опишите|подскажите|сообщите)[^.!?\n]*'
    r'(?:что\s+именно[^.!?\n]*(?:не\s+устроило|не\s+понравилось)|'
    r'какие\s+свойства[^.!?\n]*не\s+устроили|в\s+ч[её]м\s+(?:проявилась|заключается)\s+проблема|'
    r'(?:детали|подробности|суть|причину)\s+(?:Вашей\s+)?(?:проблемы|жалобы|недовольства)|'
    r'(?:ситуацию|проблему))[^.!?\n]*[.!?]?', re.I)


def has_redundant_clarification(reply, buyer_text):
    return bool(analyze_customer_message(buyer_text)['complaints'] and REDUNDANT_CLARIFICATION.search(reply))


def remove_redundant_clarification(reply, buyer_text):
    return REDUNDANT_CLARIFICATION.sub('', reply) if analyze_customer_message(buyer_text)['complaints'] else reply


def missing_complaints(analysis, reply):
    value = reply.lower().replace('ё', 'е')
    return [kind for kind in analysis['complaints'] if not any(x in value for x in TOPIC_WORDS[kind])]


def semantic_reply_issues(review, product, reply):
    """Independent hard checks: a high courtesy score cannot hide a contradiction."""
    from .product_guidance import is_cleaner, positive_detail
    analysis = analyze_customer_message(review.text, review.rating)
    text = reply.lower().replace('ё', 'е')
    buyer = (review.text or '').lower().replace('ё', 'е')
    name = (getattr(product, 'name', '') + ' ' + getattr(product, 'category', '')).lower().replace('ё', 'е')
    issues = []
    if buyer.strip() and not analysis['complaints'] and analysis['sentiment'] == 'positive' and re.search(r'жаль|сожале|извин|не оправдал|не подош[её]л|не понравил|разочар', text):
        issues.append('не трактовать положительный отзыв как жалобу')
    if analysis['complaints'] and analysis['sentiment'] == 'negative' and re.search(r'рады, что (?:покупка|товар|аромат).*понравил', text):
        issues.append('не благодарить за довольство при жалобе')
    if 'аромат вам понравился' in text and 'аромат Вам понравился' not in positive_detail(buyer):
        issues.append('не приписывать покупателю положительное впечатление об аромате')
    if not is_cleaner(product) and re.search(r'дозировк|время выдержки|разных состав|какую поверхность и загрязнение', text):
        issues.append('совет по чистящей химии не относится к этому товару')
    if set(analysis['complaints']) == {'PACKAGING_ISSUE'} and re.search(r'повреждение (?:полученного )?товара|товар оказался поврежд', text):
        issues.append('не подменять повреждение упаковки повреждением товара')
    if analysis.get('resolved') and re.search(r'пришлите|приложите|фотограф|через полученный заказ|повторно описывать', text):
        issues.append('учесть, что покупатель уже решил проблему; не добавлять требования')
    if not buyer.strip() and re.search(r'по инструкции|распылите|дозировк|время выдержки|«.{60}', text):
        issues.append('для оценки без текста нужна краткая благодарность без инструкции и полного названия')
    if re.search(r'мы используем передовые технологии|не нанося.*вреда природе', text):
        issues.append('не выдавать рекламное описание за инструкцию')
    if 'аккумулятор' in name and 'протечку средства' in text:
        issues.append('использовать термин электролит для аккумулятора')
    if buyer.strip() and not analysis['complaints'] and not analysis['is_question'] and re.search(r'по инструкции к этому товару|рекомендуем соблюдать способ применения|притирк', text):
        issues.append('не добавлять инструкцию без вопроса или жалобы')
    if 'вас устроила цена' in text and not favorable_price(buyer):
        issues.append('не считать упоминание цены подтверждением довольства')
    if 'giga' in text and not re.search(r'giga|стерж|картридж', name):
        issues.append('не переносить рекомендации GIGA на другой тип ароматизатора')
    if re.search(r'(?:фиксируется|прошла|порадовал)[^.!?]{0,30}\sи оценили', text):
        issues.append('исправить согласование частей предложения')
    partial = re.search(r'\b(\d+)\s+из\s+(\d+)\b', buyer)
    if partial and 0 < int(partial[1]) < int(partial[2]) and re.search(r'все\s+аромат\w*[^.!?]{0,25}понрав', text):
        issues.append('не заменять частичное одобрение похвалой всему набору')
    if buyer.strip() and len(re.findall(r'спасибо|благодарим', text)) > 1:
        issues.append('убрать повторную благодарность')
    if analysis.get('resolved') and re.search(r'одобрил\w*\s+возврат', buyer) and not re.search(r'одобр|решен|решён|вопрос.*реш', text):
        issues.append('учесть уже одобренный возврат')
    return issues


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
        'SIZE_ISSUE': 'длина товара оказалась для Вас недостаточной',
    }
    details = [clauses[kind] for kind in analysis['complaints']]
    if not details:
        return None
    thanks = ('Спасибо за обратную связь.', 'Благодарим, что рассказали о ситуации.', 'Спасибо за Ваш отзыв.')[revision % 3]
    if analysis['buyer_asks_about_money']:
        ending = 'Для дополнительной проверки можно обратиться в официальную поддержку маркетплейса.'
    else:
        ending = 'Вопрос с возвратом уже решён. Сожалеем о доставленных неудобствах.' if re.search(r'одобрил\w*\s+возврат', analysis['buyer_text'], re.I) else 'Сожалеем о доставленных неудобствах.'
    return 'Здравствуйте! ' + thanks + ' Приносим извинения за то, что ' + '; '.join(details) + '. ' + ending
