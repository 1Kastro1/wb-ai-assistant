"""Product-specific advice from the same trusted facts used by generation."""
import re


def is_cleaner(product):
    text = (getattr(product, 'name', '') + ' ' + getattr(product, 'category', '')).lower()
    return any(x in text for x in ('очистител', 'чистящ', 'моющ', 'пятновывод', 'химчист', 'бытовая хим', 'автохим'))


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
    from .reply_policy import analyze_customer_message
    analysis = analyze_customer_message(review.text, review.rating)
    if getattr(review, 'risk', 'NORMAL') != 'NORMAL' or analysis['buyer_asks_about_money']:
        return None
    complaints = set(analysis['complaints'])
    name = (getattr(product, 'name', '') + ' ' + getattr(product, 'category', '')).lower()
    text = review.text.lower().replace('ё', 'е')
    halogen = 'ламп' in name and 'галоген' in name
    dim = any(marker in text for marker in ('тускл', 'слабо свет', 'свет слаб')) and not re.search(r'(?<!\w)не\s+тускл', text)
    if halogen and dim and not complaints - {'DESCRIPTION_MISMATCH', 'NO_RESULT', 'DIM_LIGHT'}:
        # General lighting principle, not a diagnosis of this buyer's car:
        # https://www.hella.co.nz/en/about-us/technology/automotive-bulbs/maximising-bulb-life/
        thanks = ['Спасибо за отзыв.', 'Благодарим за обратную связь.', 'Спасибо, что рассказали о впечатлении.'][int(revision) % 3]
        color = ' и жёлтым' if 'желт' in text else ''
        return f'Здравствуйте! {thanks} Извините, что свет оказался тусклым{color} и не оправдал Ваших ожиданий. При недостаточном напряжении питания галогенная лампа светит слабее — это одна из возможных причин, которую стоит проверить. Рекомендуем в сервисе проверить напряжение на лампах и их установку, а также сверить маркировку с заявленными характеристиками.'
    cleaner_quality = is_cleaner(product) and any(marker in text for marker in ('плохое качество', 'качество плохое', 'некачеств'))
    allowed_complaints = {'NO_RESULT', 'ODOR_COMPLAINT', 'WEAK_SCENT'} | ({'QUALITY_ISSUE'} if cleaner_quality else set())
    if complaints - allowed_complaints:
        return None
    smoke_product = any(marker in name for marker in ('дымовая шашка', 'паровая шашка'))
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
        if 'WEAK_SCENT' in complaints:
            parts.append('Сожалеем, что аромат ощущается слабее, чем Вы ожидали.')
            # Never tell the owner to attach a loose can or refill to a vent.
            parts.append('Попробуйте расположить ароматизатор ближе к потоку воздуха: на дефлекторе его можно закрепить только предусмотренным для Вашей модели держателем, соблюдая инструкцию на упаковке; баночку или сменный картридж нельзя крепить к решётке без подходящего держателя.')
        if 'ODOR_COMPLAINT' in complaints:
            parts.append('Нам жаль, что запах Вам не подошёл; восприятие ароматов индивидуально, поэтому можно выбрать другую композицию по Вашим предпочтениям.')
        return ' '.join(parts)
    if is_cleaner(product):
        instruction = usage_excerpt(facts)
        parts = [intro]
        if analysis['sentiment'] == 'negative':
            concern = 'качество средства Вас разочаровало' if cleaner_quality else 'результат очистки Вас разочаровал'
            parts.append('Извините, что ' + concern + (' и запах оказался неприятным.' if 'ODOR_COMPLAINT' in complaints else '.'))
        elif analysis['sentiment'] == 'positive':
            parts.append('Рады, что Вы остались довольны покупкой.')
        if instruction:
            already_followed = any(marker in text for marker in ('по инструкции', 'по инструкц', 'согласно инструкц'))
            prefix = 'Вы уже следовали инструкции; для сверки приведём способ применения производителя: ' if already_followed else 'Рекомендуем соблюдать способ применения и время выдержки, указанные производителем: '
            parts.append(prefix + instruction)
            if analysis['sentiment'] == 'negative':
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
