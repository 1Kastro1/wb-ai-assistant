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
    if complaints - {'NO_RESULT', 'ODOR_COMPLAINT', 'WEAK_SCENT'}:
        return None
    name = (getattr(product, 'name', '') + ' ' + getattr(product, 'category', '')).lower()
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
            parts.append('Извините, что результат очистки Вас разочаровал' + (' и запах оказался неприятным.' if 'ODOR_COMPLAINT' in complaints else '.'))
        elif analysis['sentiment'] == 'positive':
            parts.append('Рады, что Вы остались довольны покупкой.')
        if instruction:
            parts.append('По инструкции к этому товару: ' + instruction)
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
