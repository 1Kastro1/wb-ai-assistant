import re
from .product_guidance import product_usage_reply, is_cleaner, positive_review_reply
from .reply_policy import analyze_customer_message, contextual_complaint_reply, official_support_reply, strip_other_product_comparisons

VIN = re.compile(r'\b[A-HJ-NPR-Z0-9]{17}\b', re.I)
HIGH = ('хуже тормоз', 'плохо тормоз', 'ухудшен', 'провалива', 'дым', 'перегрев', 'заклини', 'утеч', 'скрежет', 'сильн', 'не тормоз', 'отказ тормоз')
CRITICAL = ('тормоз', 'колод', 'суппорт', 'рулев', 'шаров', 'ступиц', 'подвеск', 'диск')
FORBIDDEN = ('это нормально', 'притрутся', 'неправильно установили', 'плохие диски', 'точно брак', 'точно не брак', 'продолжайте ездить', 'гарантируем', 'компенсац')


def _contains_any(value, phrases):
    # A bare substring would interpret «мне понравился» as «не понравился».
    return any(bool(re.search(r'(?<!\w)' + re.escape(phrase), value)) if phrase.startswith('не ')
               else phrase in value for phrase in phrases)


def review_intent(text):
    value = ' '.join(strip_other_product_comparisons(text).lower().replace('ё', 'е').split())
    if _contains_any(value, ('не тот товар', 'другой товар', 'пришел не тот', 'прислали не тот', 'получил не тот')):
        return 'WRONG_ITEM'
    if _contains_any(value, ('нет детал', 'не хватает', 'неполная комплектац', 'не доложили')):
        return 'MISSING_PARTS'
    if any(x in value for x in ('упаковка поврежд', 'упаковка помят', 'коробка помят', 'плохо упакован', 'упаковка вскрыт')):
        return 'PACKAGING_ISSUE'
    if any(x in value for x in ('сломан', 'разбит', 'поврежден', 'помят')):
        return 'DAMAGED_ITEM'
    if _contains_any(value, ('не подошел', 'не подходит', 'несовместим')):
        return 'FITMENT_PROBLEM'
    if _contains_any(value, ('не увидел результата', 'не увидела результата', 'нет результата', 'не помог', 'не помогло', 'не очищает', 'не очистил', 'не отмыл', 'не отмыла', 'деньги на ветер', 'толку нет')):
        return 'NO_RESULT'
    if 'воняет' in value or (any(x in value for x in ('запах', 'аромат')) and _contains_any(value, ('неприят', 'резк', 'ужасн', 'не понрав'))):
        return 'ODOR_COMPLAINT'
    if _contains_any(value, ('запаха почти нет', 'слабый запах', 'слабый аромат', 'не пахнет', 'аромат не чувств', 'запах не чувств', 'почти не пах', 'еле пах', 'нет запаха')):
        return 'WEAK_SCENT'
    if _contains_any(value, ('не соответствует описанию', 'не как на фото', 'отличается от описания', 'характеристики не совпадают')):
        return 'DESCRIPTION_MISMATCH'
    if origin_unclear_complaint(text):
        return 'DESCRIPTION_MISMATCH'
    if any(x in value for x in ('долго достав', 'задержали достав', 'проблема с достав', 'курьер', 'пункт выдачи')):
        return 'DELIVERY_ISSUE'
    if any(x in value for x in ('как пользоваться', 'как использовать', 'как применять', 'как наносить', 'способ применения', 'подскажите')) or '?' in value:
        return 'USAGE_QUESTION'
    if _contains_any(value, ('отличный', 'понравил', 'рекомендую', 'справился', 'помог', 'подошел', 'хороший товар', 'все супер', 'всё супер', 'супер', 'чистит все', 'очистил', 'отмыл', 'приятн', 'ненавязчив')):
        return 'POSITIVE_EXPERIENCE'
    return 'GENERAL'


def origin_unclear_complaint(text):
    value = ' '.join(text.lower().replace('ё', 'е').split())
    origin = any(x in value for x in ('производител', 'япони', 'китай', 'страна', 'оригинал'))
    unclear = any(x in value for x in ('х/з', 'хз', 'непонят', 'не извест', 'неизвест', 'другая стран', 'без маркиров', 'нет маркиров'))
    received = any(x in value for x in ('приш', 'получ', 'присла', 'достав'))
    return origin and unclear and received


def description_mismatch_reply(revision=0, text=''):
    if text and not origin_unclear_complaint(text):
        return contextual_complaint_reply(analyze_customer_message(text), revision)
    return _variant([
        'Здравствуйте! Спасибо за отзыв. Приносим извинения за разочарование от покупки. Если маркировка товара отличается от указанной в заказе, приложите фотографии товара и упаковки к обращению в поддержку маркетплейса для проверки.',
        'Здравствуйте! Благодарим за обратную связь. Извините, что полученный товар не оправдал Ваших ожиданий. Для уточнения соответствия заказу можно направить в поддержку маркетплейса фотографии маркировки товара и упаковки.',
        'Здравствуйте! Спасибо, что рассказали о ситуации. Приносим извинения за неприятное впечатление от покупки. Если есть расхождения с заказом, укажите их в обращении в поддержку маркетплейса и приложите фотографии маркировки товара и упаковки.',
    ], revision)


def fresh_reply_variants(candidates, previous=''):
    """Prefer a different checked text while preserving mandatory single replies."""
    alternatives = [value for value in candidates if value.strip() != previous.strip()]
    return alternatives or candidates


def mask_vin(text):
    return VIN.sub(lambda m: m[0][:3] + '***********' + m[0][-3:], text)


def classify(text, category=''):
    value = (text + ' ' + category).lower()
    critical = any(x in value for x in CRITICAL)
    high = critical and any(x in text.lower() for x in HIGH)
    caution = critical and any(x in text.lower() for x in ('скрип', 'свист', 'шум'))
    return {'risk': 'HIGH' if high else 'CAUTION' if caution else 'NORMAL', 'manual': high, 'safety_critical': critical,
            'topics': [x for x in ('упаков', 'достав', 'запах', 'скрип', 'вибрац', 'тормоз', 'подбор', 'совместим') if x in value] or ['другое']}


def validate_reply(text):
    if not 2 <= len(text.strip()) <= 5000:
        raise ValueError('Ответ должен содержать от 2 до 5000 символов')
    if VIN.search(text):
        raise ValueError('Полный VIN запрещён в публичном ответе')
    if any(x in text.lower() for x in FORBIDDEN):
        raise ValueError('Ответ содержит запрещённое обещание или небезопасное утверждение')
    if re.search(r'просроч|(?:ист[её]к|законч|выш[её]л)[^.!?]{0,35}срок\w*\s+годност|срок\w*\s+годност[^.!?]{0,35}(?:ист[её]к|законч|выш[её]л)', text, re.I):
        raise ValueError('Не обсуждать просроченность товара и не предполагать её причиной проблемы')
    if re.search(r'(?:верн|возврат)\w*[^.!?]{0,45}(?:деньг|средств|стоим|оплат)|возмещ\w*[^.!?]{0,35}(?:расход|стоим)|(?:выплат|перевед|зачисл)[^.!?]{0,80}(?:счет|карт|деньг|руб|₽)|(?:нет|не имеете|отсутствуют)[^.!?]{0,25}(?:права|право)', text, re.I):
        raise ValueError('Не обсуждать финансовые условия и не отрицать права покупателя')
    if re.search(r'заяв\w*[^.!?]{0,60}возврат|оформ\w*[^.!?]{0,30}возврат', text, re.I):
        raise ValueError('Не предлагать заявку на возврат в публичном ответе')
    if re.search(r'учт[её]м[^.!?]{0,140}(?:недостат|замечан|отзыв|информац|карточк|товар)|поможет точнее описывать', text, re.I):
        raise ValueError('Вместо обещания учесть отзыв нужны извинение и полезный следующий шаг')
    if re.search(r'производител[^.!?]{0,100}(?:был понятен|должен быть ясно указан)|производитель и маркировка указаны понятно', text, re.I):
        raise ValueError('Не использовать абстрактные фразы о понятности производителя; ответить на ситуацию без выдуманных причин')
    service_markers = (
        'previous_draft', 'review_data', 'detected_intent', 'owner_style_profile',
        'важно не повторять предыдущий', 'нужно сделать ответ', 'ответ должен быть содержательным',
        'вот мой анализ', 'служебная инструкция', '/no_think', 'internal_analysis', 'primary_complaint', 'buyer_asks_about_money',
    )
    if any(marker in text.lower() for marker in service_markers):
        raise ValueError('Ответ содержит служебные рассуждения вместо текста покупателю')
    if re.search(r'(?i)\b(?:мне|нам)\s+(?:нужно|надо|следует)\s+(?:сначала\s+)?(?:создать|сгенерировать|написать|подготовить)\b[^.!?\n]{0,120}\bвариант\w*\s+ответ\w*', text):
        raise ValueError('Ответ содержит служебные рассуждения вместо текста покупателю')
    if re.search(r'совместимость\s+подтверждена|(?:точно|полностью|гарантированно)\s+под(?:ходит|ойдут)|подходит\s+(?:к|для|на)\s+ваш', text, re.I):
        raise ValueError('Подтверждать применяемость можно только по проверенному результату каталога')
    text = re.sub(r'\b(вы|вас|вам|вами|ваш[а-я]*)\b', lambda m: m.group(0)[0].upper() + m.group(0)[1:], text, flags=re.I)
    return text.strip()


def _variant(options, revision=0):
    return options[max(0, int(revision)) % len(options)]


def cleaner_effectiveness_complaint(review, product):
    text = review.text.lower().replace('ё', 'е')
    product_text = (getattr(product, 'name', '') + ' ' + getattr(product, 'category', '')).lower().replace('ё', 'е')
    cleaner = any(marker in product_text for marker in ('очистител', 'пятн', 'химчист'))
    dissatisfied = review_intent(review.text) == 'NO_RESULT' or any(marker in text for marker in (
        'полная хрень', 'полный отстой', 'бесполезн', 'не работает', 'неэффектив', 'толку нет', 'деньги на ветер',
    ))
    return cleaner and review.rating <= 3 and dissatisfied


def rule_reply(review, product, revision=0, facts=None):
    analysis = analyze_customer_message(review.text, review.rating)
    if not review.text.strip():
        if review.rating >= 4:
            from .product_guidance import usage_excerpt
            trusted_facts = facts if facts is not None else (getattr(product, 'facts', None) or [])
            if is_cleaner(product) and 'набор' not in product.name.lower() and usage_excerpt(trusted_facts):
                return product_usage_reply(review, product, trusted_facts, revision)
            name = getattr(product, 'name', 'товар')[:120]
            return _variant([
                f'Здравствуйте! Спасибо за высокую оценку товара «{name}». Нам приятно, что Вы выбрали наш магазин. Желаем Вам приятного использования!',
                f'Здравствуйте! Благодарим за оценку товара «{name}». Рады, что Вы остались довольны покупкой. Спасибо за доверие и желаем приятного использования!',
                f'Здравствуйте! Спасибо, что оценили товар «{name}». Ваша обратная связь важна для нас. Желаем Вам приятного использования и благодарим за выбор!',
            ], revision)
        return 'Здравствуйте! Спасибо за оценку. Нам искренне жаль, что товар не оправдал ваших ожиданий. Нам важно понять, что можно улучшить. Если будет возможность, дополните отзыв и расскажите, пожалуйста, что именно вас не устроило.'
    if review_intent(review.text) == 'WRONG_ITEM' and len(analysis['complaints']) <= 1:
        return 'Здравствуйте! Приносим искренние извинения за то, что вам пришёл не тот товар. Понимаем ваше разочарование: вы ожидали получить именно выбранный товар. Спасибо, что сообщили нам об этой ситуации.'
    if review.risk == 'HIGH':
        return 'Здравствуйте! Спасибо, что сообщили о проблеме. Нам искренне жаль, что возникла такая ситуация. Описанные симптомы требуют проверки тормозной системы специалистом до дальнейшей эксплуатации. По отзыву установить причину невозможно. Пожалуйста, обратитесь в сервис для диагностики.'
    if review.risk == 'CAUTION':
        return 'Здравствуйте! Спасибо за обратную связь. Нам жаль, что после установки появился скрип. У скрипа тормозных колодок могут быть разные причины, и по одному отзыву установить их невозможно. Рекомендуем проверить тормозную систему и правильность установки в сервисе. После диагностики будет проще определить дальнейшие действия.'
    if analysis['buyer_asks_about_money']:
        return contextual_complaint_reply(analysis, revision) or official_support_reply()
    guidance = product_usage_reply(review, product, facts if facts is not None else (getattr(product, 'facts', None) or []), revision)
    if guidance:
        return guidance
    positive = positive_review_reply(review, product, revision)
    if positive:
        return positive
    if len(analysis['complaints']) > 1:
        return None
    intent = review_intent(review.text)
    if intent == 'DESCRIPTION_MISMATCH':
        return description_mismatch_reply(revision, review.text)
    if review.rating >= 4 and intent == 'POSITIVE_EXPERIENCE' and any(x in review.text.lower() for x in ('пах', 'аромат', 'запах')):
        return _variant([
            'Здравствуйте! Спасибо за высокую оценку и за то, что поделились впечатлением. Очень рады, что аромат вам понравился. Благодарим за выбор нашего товара и желаем приятных поездок!',
            'Здравствуйте! Благодарим за тёплый отзыв об аромате и высокую оценку. Нам приятно знать, что товар пришёлся вам по вкусу. Пользуйтесь с удовольствием и спасибо, что выбрали нас!',
            'Здравствуйте! Спасибо, что рассказали о своём впечатлении и оценили аромат. Рады, что покупка вам понравилась. Благодарим за доверие и желаем приятного использования!',
        ], revision)
    product_text = (product.name + ' ' + product.category).lower()
    if cleaner_effectiveness_complaint(review, product):
        # A combined complaint about effectiveness and smell needs the verified usage
        # facts from the product card, which are handled by the full generation path.
        return None
    if intent == 'ODOR_COMPLAINT' and any(x in product_text for x in ('аромат', 'парфюм', 'запах')):
        return _variant([
            'Здравствуйте! Спасибо, что честно поделились впечатлением. Нам очень жаль, что запах ароматизатора оказался для вас неприятным и аромат вам не понравился. Восприятие ароматов индивидуально, поэтому одна и та же композиция может ощущаться по-разному. Возможно, вам больше подойдёт другой запах из ассортимента — можно выбрать более свежий, лёгкий или спокойный вариант. Благодарим за обратную связь — она помогает другим покупателям лучше понять особенности товара.',
            'Здравствуйте! Благодарим вас за откровенный отзыв. Искренне сожалеем, что аромат показался вам резким и оставил неприятное впечатление. Поскольку восприятие запахов у каждого человека своё, советуем в следующий раз выбрать другой аромат с более подходящей для вас композицией. Надеемся, среди других вариантов найдётся запах, который будет радовать вас в автомобиле.',
            'Здравствуйте! Спасибо, что рассказали о своём опыте. Нам жаль, что запах этого ароматизатора совершенно вам не подошёл. Ароматы воспринимаются очень индивидуально, поэтому предлагаем обратить внимание на другие запахи и выбрать композицию по своему вкусу. Ваша честная обратная связь важна для нас и для покупателей, которые выбирают аромат.',
        ], revision)
    if intent == 'ODOR_COMPLAINT' and any(x in review.text.lower() for x in ('пах', 'запах', 'воня')):
        return _variant([
            f'Здравствуйте! Спасибо за честный отзыв о товаре «{product.name}». Нам искренне жаль, что средство не оправдало ваших ожиданий, а его запах показался неприятным. Приносим извинения за неприятное впечатление от использования. Благодарим, что поделились своим впечатлением.',
            f'Здравствуйте! Благодарим за обратную связь о товаре «{product.name}». Сожалеем, что результат использования вас разочаровал и запах средства оставил неприятное впечатление. Приносим извинения за неприятное впечатление от покупки. Спасибо, что рассказали о своём опыте.',
            f'Здравствуйте! Спасибо, что поделились мнением о товаре «{product.name}». Нам очень жаль, что средство вам не понравилось, в том числе из-за его запаха. Приносим извинения за разочарование от покупки. Благодарим за честную обратную связь.',
        ], revision)
    if any(kind in analysis['complaints'] for kind in ('MISSING_PARTS', 'DAMAGED_ITEM', 'QUALITY_ISSUE', 'PACKAGING_ISSUE', 'DELIVERY_ISSUE', 'FITMENT_PROBLEM')):
        return contextual_complaint_reply(analysis, revision)
    if review.rating >= 4 and any(x in review.text.lower() for x in ('смол', 'гудрон')) and 'очист' in product_text:
        return safe_fallback_reply(review, product)
    return None


def safe_fallback_reply(review, product, revision=0, facts=None):
    if review.risk in ('HIGH', 'CAUTION'):
        return rule_reply(review, product, revision, facts)
    guidance = product_usage_reply(review, product, facts if facts is not None else (getattr(product, 'facts', None) or []), revision)
    if guidance:
        return guidance
    analysis = analyze_customer_message(review.text, review.rating)
    if review.risk == 'NORMAL' and (analysis['buyer_asks_about_money'] or len(analysis['complaints']) > 1):
        return contextual_complaint_reply(analysis, revision) or official_support_reply()
    if review_intent(review.text) == 'DESCRIPTION_MISMATCH' and review.risk == 'NORMAL':
        return description_mismatch_reply(revision, review.text)
    text = review.text.lower()
    product_text = (getattr(product, 'name', '') + ' ' + getattr(product, 'category', '')).lower()
    facts_text = ' '.join(str(f.get('text', '')) for f in (facts if facts is not None else (getattr(product, 'facts', None) or [])) if f.get('verification_status') == 'VERIFIED').lower()
    if review.rating >= 4 and analysis['sentiment'] not in ('negative', 'mixed'):
        return positive_review_reply(review, product, revision)
    return _variant([
        'Здравствуйте! Спасибо за отзыв. Извините, что покупка не оправдала Ваших ожиданий. Уточните, пожалуйста, что именно Вас не устроило, чтобы можно было дать подходящую рекомендацию.',
        'Здравствуйте! Благодарим за обратную связь. Приносим извинения за разочарование от покупки. Расскажите, пожалуйста, в чём проявилась проблема с товаром, чтобы можно было разобраться в Вашей ситуации.',
        'Здравствуйте! Спасибо, что поделились впечатлением. Извините за неприятный опыт использования. Уточните, пожалуйста, какие свойства или работа товара Вас не устроили — это поможет подобрать полезный совет.',
    ], revision)
