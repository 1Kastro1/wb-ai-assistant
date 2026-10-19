"""Product-specific advice from the same trusted facts used by generation."""
import re


def is_cleaner(product):
    text = (getattr(product, 'name', '') + ' ' + getattr(product, 'category', '')).lower()
    return any(x in text for x in ('очистител', 'чистящ', 'моющ', 'пятновывод', 'химчист', 'бытовая хим', 'автохим', 'полироль', 'воск автомобильный'))


def positive_detail(text):
    """Acknowledge the buyer's experience without asserting new product facts."""
    value = text.lower().replace('ё', 'е')
    details = []
    if any(x in value for x in ('аромат', 'запах', 'пахнет', 'пахн')):
        details.append('аромат Вам понравился')
    if 'пенится' in value:
        details.append('Вы отметили хорошее пенообразование')
    if any(x in value for x in ('отмывает', 'отмыл', 'чистит', 'очистил', 'нагар', 'гудрон', 'смол')):
        details.append('результат очистки Вас порадовал')
    elif any(x in value for x in ('отработал', 'работает', 'рабочими')):
        details.append('Вы довольны работой товара')
    if any(x in value for x in ('подош', 'встали как родные')):
        details.append('установка прошла успешно по Вашему опыту')
    if any(x in value for x in ('качеств', 'на совесть', 'клапан', 'резьба')):
        details.append('Вы оценили качество товара')
    if 'соответствует описанию' in value:
        details.append('товар соответствует Вашим ожиданиям и описанию')
    if any(x in value for x in ('третий раз', 'не первый раз', 'пользуюсь давно')):
        details.append('Вы снова выбираете этот товар')
    if any(x in value for x in ('вовремя', 'во время')):
        details.append('заказ пришёл вовремя')
    return 'Рады, что ' + ' и '.join(details[:2]) + '.' if details else 'Рады, что покупка Вам понравилась.'


def positive_review_reply(review, product, revision=0):
    from .reply_policy import analyze_customer_message
    analysis = analyze_customer_message(review.text, review.rating)
    if review.rating < 4 or analysis['sentiment'] in ('negative', 'mixed'):
        return None
    thanks = ['Спасибо за Ваш отзыв.', 'Благодарим за обратную связь.', 'Спасибо, что поделились опытом.'][int(revision) % 3]
    return 'Здравствуйте! ' + thanks + ' ' + positive_detail(review.text) + ' Благодарим за выбор нашего магазина и желаем приятного использования!'


def battery_voltage_answer(question, product):
    """Conditional general benchmark; never claim this unit's measured charge."""
    name, text = product.name.lower(), question.text.lower()
    if 'аккумулятор' not in name or not ('клемм' in text and any(x in text for x in ('вольт', 'напряжен'))):
        return None
    if any(x in name for x in ('литиев', 'литий', '24в', '24 в', '6в', '6 в')):
        return None
    return {
        'answer': 'Здравствуйте! Если речь о 12-вольтовом свинцово-кислотном аккумуляторе, ориентир для полностью заряженного АКБ в покое — примерно 12,6–12,8 В на клеммах. Номинальные 12 В и фактически измеренное напряжение заряда — разные показатели. При покупке попросите проверить напряжение мультиметром: заряд конкретного экземпляра нельзя определить по названию товара.',
        'source': {'text': 'Общий ориентир GS Yuasa: полностью заряженный 12-вольтовый АКБ в покое — примерно 12,6–12,8 В; это не измерение продаваемого экземпляра.', 'source': 'https://www.yuasa.com/en-it/faq/question/view/id/464/', 'verification_status': 'VERIFIED', 'source_type': 'technical_reference'},
    }


def usage_excerpt(facts):
    """Keep actual instructions, including model-specific times and restrictions."""
    from .verified_instructions import prefer_manufacturer_instructions
    facts = prefer_manufacturer_instructions(facts or [])
    primary = [f for f in facts if f.get('kind') == 'instruction' and f.get('source_type') == 'manufacturer']
    if primary:
        facts = primary
    result = []
    for fact in facts:
        if fact.get('verification_status') != 'VERIFIED':
            continue
        text = str(fact.get('text', '')).strip()
        heading = re.search(r'(?:способ применения|инструкция по применению|применение)\s*:', text, re.I)
        if heading:
            text = text[heading.end():]
            text = re.split(r'\n\s*(?:состав|хранение|характеристики|артикул)\s*:', text, flags=re.I)[0]
        for sentence in re.split(r'(?<=[.!?])\s+|\n+', text):
            sentence = sentence.strip(' •-\t')
            if not sentence:
                continue
            action = re.search(r'нанес|нанос|распыл|встрях|протр|протир|потрите|смой|смыв|смыть|выдерж|оставьте|удалите|обработ|не допуска|не использ|не примен|проверьте|проверить|установ|размест|закреп|открой|открыть', sentence, re.I)
            if (heading or action) and sentence not in result and len(' '.join(result + [sentence])) <= 2000:
                result.append(sentence)
    return '; '.join(sentence.rstrip('.') for sentence in result) + ('.' if result else '')


def product_usage_reply(review, product, facts, revision=0):
    from .reply_policy import analyze_customer_message, faded_scent, yellow_light_complaint
    analysis = analyze_customer_message(review.text, review.rating)
    if getattr(review, 'risk', 'NORMAL') != 'NORMAL' or analysis['buyer_asks_about_money']:
        return None
    complaints = set(analysis['complaints'])
    if review.rating == 5 and analysis['sentiment'] == 'positive' and not analysis['is_question']:
        return positive_review_reply(review, product, revision)
    name = (getattr(product, 'name', '') + ' ' + getattr(product, 'category', '')).lower()
    text = review.text.lower().replace('ё', 'е')
    if complaints & {'LEAKAGE', 'PACKAGING_ISSUE'} and not complaints - {'LEAKAGE', 'PACKAGING_ISSUE', 'ODOR_COMPLAINT'}:
        thanks = ['Спасибо за отзыв.', 'Благодарим за обратную связь.', 'Спасибо, что рассказали о ситуации.'][int(revision) % 3]
        parts = ['Здравствуйте! ' + thanks]
        if analysis['sentiment'] == 'mixed':
            parts.append('Рады, что само средство Вам понравилось.')
        details = ['ненадлежащее состояние крышки и упаковки' if 'крышк' in text else 'ненадлежащее состояние упаковки']
        if 'LEAKAGE' in complaints:
            details.append('протечку средства')
        if 'ODOR_COMPLAINT' in complaints:
            details.append('неприятный запах')
        parts.append('Приносим извинения за ' + (', '.join(details[:-1]) + ' и ' + details[-1] if len(details) > 1 else details[0]) + '.')
        parts.append('Если при получении заказа в ПВЗ Вы обнаружите повреждение упаковки или несоответствие товара описанию, Вы можете отказаться от его получения.')
        return ' '.join(parts)
    halogen = 'ламп' in name and 'галоген' in name
    if halogen and yellow_light_complaint(text) and not complaints - {'DESCRIPTION_MISMATCH','NO_RESULT','DIM_LIGHT'}:
        temperatures = set(re.findall(r'\b([2-9][0-9]{3})\s*[кk]\b', name, re.I))
        for fact in facts or []:
            if fact.get('verification_status')=='VERIFIED':
                temperatures.update(re.findall(r'цветов\w*\s+температур\w*[^0-9]{0,20}([2-9][0-9]{3})',fact.get('text',''),re.I))
        dim_complaint=bool(re.search(r'тускл|слабо свет|свет слаб',text)) and not re.search(r'(?<!\w)не\s+тускл',text)
        intro = 'Добрый день! Спасибо за отзыв. Извините, что свет оказался тусклым и жёлтым и не оправдал Ваших ожиданий. ' if dim_complaint else 'Добрый день! Спасибо за отзыв. Извините, что оттенок света не оправдал Ваших ожиданий. '
        brightness = ' При недостаточном напряжении питания галогенная лампа светит слабее — это одна из возможных причин, которую стоит проверить в сервисе вместе с установкой ламп и состоянием оптики.' if dim_complaint else ''
        if len(temperatures)==1:
            kelvin = int(next(iter(temperatures)))
            explanation = 'это тёплый оттенок света, поэтому желтоватое свечение ожидаемо для такой характеристики.' if kelvin<3800 else 'такой свет теплее, чем у ламп 5000–6000 K, поэтому лёгкий желтоватый оттенок возможен.' if kelvin<=4500 else 'это характеристика более холодного белого оттенка; выраженную желтизну не следует автоматически считать особенностью модели.'
            return intro+'В карточке этой галогенной лампы указана цветовая температура '+str(kelvin)+' K: '+explanation+' Кельвины характеризуют цвет света, а не яркость. Если оттенок заметно отличается от заявленного, сверьте маркировку ламп и упаковки с карточкой товара.'+brightness
        return intro+'Для объяснения оттенка нужно сопоставить цветовую температуру на упаковке и в характеристиках этой лампы. В имеющихся данных нет единственного согласованного значения, поэтому желтизну нельзя уверенно объяснить свойством модели; сверьте маркировку с карточкой товара.'+brightness
    dim = any(marker in text for marker in ('тускл', 'слабо свет', 'свет слаб')) and not re.search(r'(?<!\w)не\s+тускл', text)
    if halogen and dim and not complaints - {'DESCRIPTION_MISMATCH', 'NO_RESULT', 'DIM_LIGHT'}:
        # General lighting principle, not a diagnosis of this buyer's car:
        # https://www.hella.co.nz/en/about-us/technology/automotive-bulbs/maximising-bulb-life/
        thanks = ['Спасибо за отзыв.', 'Благодарим за обратную связь.', 'Спасибо, что рассказали о впечатлении.'][int(revision) % 3]
        color = ' и жёлтым' if 'желт' in text else ''
        return f'Здравствуйте! {thanks} Извините, что свет оказался тусклым{color} и не оправдал Ваших ожиданий. При недостаточном напряжении питания галогенная лампа светит слабее — это одна из возможных причин, которую стоит проверить. Рекомендуем в сервисе проверить напряжение на лампах и их установку, а также сверить маркировку с заявленными характеристиками.'
    cleaner_quality = is_cleaner(product) and any(marker in text for marker in ('плохое качество', 'качество плохое', 'некачеств'))
    allowed_complaints = {'NO_RESULT', 'ODOR_COMPLAINT', 'WEAK_SCENT'} | ({'QUALITY_ISSUE'} if cleaner_quality else set()) | ({'RESIDUE'} if 'полироль' in name else set())
    if complaints - allowed_complaints:
        return None
    smoke_product = any(marker in name for marker in ('дымовая шашка', 'паровая шашка'))
    if smoke_product:
        from .steam_deo_guidance import steam_deo_reply
        reviewed_reply = steam_deo_reply(review, product, revision)
        if reviewed_reply:
            return reviewed_reply
    no_smoke = bool(re.search(r'(?:дым|пар)\w*\s+(?:так и\s+)?не\s+(?:пош|ид|появ|выдел)|нет\s+(?:дыма|пара)|не\s+(?:пош|появ)\w*\s+(?:дым|пар)', text))
    if smoke_product and no_smoke and not complaints - {'NO_RESULT'}:
        thanks = ['Спасибо за отзыв.', 'Благодарим за обратную связь.', 'Спасибо, что рассказали о ситуации.'][int(revision) % 3]
        detail = 'банка нагрелась, но дым не появился' if 'нагре' in text else 'при запуске шашки дым не появился'
        followed = ', хотя Вы следовали инструкции' if any(x in text for x in ('по инструкции', 'по инструкц', 'согласно инструкц')) else ''
        return f'Здравствуйте! {thanks} Извините, что {detail}{followed}. Причину по отзыву установить нельзя; для проверки можно обратиться в официальную поддержку маркетплейса с фотографиями упаковки и описанием запуска.'
    steam_deo = str(getattr(product, 'brand', '')).upper().replace(' ', '') == 'STEAMDEO'
    if smoke_product and steam_deo and review.rating <= 3 and any(marker in text for marker in ('дым', 'пар')) and not complaints - {'NO_RESULT'}:
        # The owner confirmed that this specific product is not meant to make an
        # intense visible cloud. Do not equate weak visible steam with success.
        thanks = ['Спасибо за отзыв.', 'Благодарим за обратную связь.', 'Спасибо, что поделились опытом.'][int(revision) % 3]
        return f'Здравствуйте! {thanks} Извините, что обработка не дала ожидаемого результата. STEAM DEO не предназначен для создания густого облака дыма: пар служит для распределения средства по салону и системе вентиляции, поэтому интенсивное паровыделение не требуется. При этом Ваше замечание об отсутствии эффекта важно рассмотреть отдельно; для проверки обратитесь в официальную поддержку маркетплейса с описанием запуска и запаха, который остался после обработки.'
    fragrance = getattr(product, 'brand', '').upper() == 'EIKOSHA' or any(x in name for x in ('ароматизатор', 'автопарфюм'))
    intro = ['Здравствуйте! Спасибо за отзыв.', 'Здравствуйте! Благодарим за обратную связь.', 'Здравствуйте! Спасибо, что поделились впечатлением.'][int(revision) % 3]
    if fragrance and complaints & {'WEAK_SCENT', 'ODOR_COMPLAINT'}:
        parts = [intro]
        if analysis['sentiment']=='mixed' and 'ODOR_COMPLAINT' not in complaints:
            parts.append('Рады, что сам аромат Вам понравился.')
        if 'WEAK_SCENT' in complaints:
            faded = faded_scent(review.text)
            parts.append('Извините, что аромат перестал ощущаться уже через неделю.' if faded and re.search(r'через\s+(?:одну\s+)?неделю\b', text) else 'Извините, что аромат быстро перестал ощущаться.' if faded else 'Сожалеем, что аромат ощущается слабее, чем Вы ожидали.')
            can = getattr(product,'brand','').upper()=='EIKOSHA' and bool(re.search(r'мелов|баноч|air spencer|\ba-?\d+',name,re.I)) and not re.search(r'giga|стерж|\bv-?\d+',name,re.I)
            if can:
                parts.append('Меловая основа отдаёт аромат постепенно: такой ароматизатор рассчитан на тонкий, ненавязчивый аромат, а его интенсивность зависит от температуры и движения воздуха.')
                holder = next((f for f in facts or [] if f.get('kind')=='vent_holder' and f.get('verification_status')=='VERIFIED'),None)
                if holder:
                    parts.append('Попробуйте разместить баночку на дефлекторе с помощью совместимого держателя — '+holder.get('article_label', 'артикул Wildberries '+holder['wb_nm_id'])+': поток воздуха поможет аромату равномернее распространяться по салону. Перед выбором держателя сверьте форму ламелей дефлектора с его инструкцией.')
                else:
                    parts.append('Разместите баночку в подходящем подстаканнике или кармане двери согласно её инструкции, оставив доступ воздуха; не ставьте её свободно на торпеду или под сиденье водителя.')
            else:
                parts.append('Попробуйте расположить ароматизатор ближе к потоку воздуха согласно его инструкции; сменные стержни GIGA устанавливаются в предназначенный для них корпус.')
        if 'ODOR_COMPLAINT' in complaints:
            parts.append('Нам жаль, что запах Вам не подошёл; восприятие ароматов индивидуально, поэтому можно выбрать другую композицию по Вашим предпочтениям.')
        return ' '.join(parts)
    if is_cleaner(product):
        if 'набор' in name:
            if analysis['sentiment'] == 'positive':
                return positive_review_reply(review, product, revision) + ' Для каждого средства в наборе соблюдайте инструкцию на его упаковке.'
            return intro + ' Извините, что результат Вас разочаровал. Уточните, пожалуйста, какое средство из набора Вы применяли и что обрабатывали: инструкции для разных составов могут отличаться.'
        instruction = usage_excerpt(facts)
        parts = [intro]
        if 'RESIDUE' in complaints:
            parts.append('Извините, что после полировки остались белые разводы и жирный слой, хотя Вы выполняли полировку по инструкции.')
        elif analysis['sentiment'] == 'negative':
            concern = 'качество средства Вас разочаровало' if cleaner_quality else 'результат очистки Вас разочаровал'
            parts.append('Извините, что ' + concern + (' и запах оказался неприятным.' if 'ODOR_COMPLAINT' in complaints else '.'))
        elif analysis['sentiment'] == 'positive':
            parts.append(positive_detail(review.text))
        if instruction:
            already_followed = any(marker in text for marker in ('по инструкции', 'по инструкц', 'согласно инструкц', 'все правильно'))
            prefix = 'Вы уже следовали инструкции; для сверки приведём способ применения производителя: ' if already_followed else 'Рекомендуем соблюдать способ применения и время выдержки, указанные производителем: '
            parts.append(prefix + instruction)
            if 'RESIDUE' in complaints:
                parts.append('Если разводы сохраняются, уточните, пожалуйста, на какой поверхности они остались, и обратитесь в официальную поддержку для проверки.')
            elif analysis['sentiment'] == 'negative':
                parts.append('Пожалуйста, уточните поверхность и загрязнение, с которыми не удалось справиться.' if already_followed else 'Если результат после применения по инструкции Вас не устроит, уточните, пожалуйста, поверхность и загрязнение, с которыми не удалось справиться.')
        else:
            parts.append('Для применения сверяйтесь с инструкцией именно на Вашей упаковке: допустимая поверхность, дозировка, время выдержки и способ удаления средства могут различаться у разных составов.')
        if analysis['sentiment'] == 'negative' and not instruction:
            parts.append('Уточните, пожалуйста, какую поверхность и загрязнение Вы обрабатывали, чтобы можно было подобрать подходящую рекомендацию.')
        return ' '.join(parts)
    if complaints == {'NO_RESULT'} or analysis['is_question']:
        instruction = usage_excerpt(facts)
        if instruction:
            return intro + ' ' + ('Сожалеем, что Вы не получили ожидаемого результата. ' if complaints else '') + 'По инструкции к этому товару: ' + instruction
    return None
