"""Specific owner-approved replies; technical statements remain conditional."""
import re
from .reply_policy import analyze_customer_message, quantity_shortage

HELLA = 'https://www.hella.com/partnerworld/us/Product-range/Lighting/Bulbs-3277/'
BREMBO = 'https://asia.bremboparts.com/america/en/support/car/maintenance/noises-and-vibrations-113941'
MOOG = 'https://www.moogparts.com/technical/bulletins/tech-tips/using-proper-stud-fasteners-for-ball-joint-tie-rod-end-repairs.html'
DENSO_WIPERS = 'https://www.denso-am.eu/news/wiper-blade-troubleshooting-from-denso-aftermarket'


def specific_review_case(review, product):
    text = ' '.join((review.text or '').lower().replace('ё', 'е').split())
    name = (getattr(product, 'name', '') + ' ' + getattr(product, 'category', '')).lower()
    analysis = analyze_customer_message(review.text, review.rating)
    thanks = 'Добрый день! Спасибо за отзыв. '
    def result(key, answer, url='', evidence=''):
        sources = [{'text': evidence, 'source': url, 'verification_status': 'VERIFIED', 'source_type': 'technical_reference'}] if url else []
        return {'key': key, 'answer': thanks + answer, 'sources': sources}

    if not text and review.rating <= 3:
        return result('rating_only', 'Сожалеем, что покупка оставила такое впечатление. Подскажите, пожалуйста, что именно Вас не устроило и почему Вы поставили такую оценку? Это поможет нам ответить по существу.')

    # Never turn braking failure, overheating or grinding into a bedding-in reply.
    braking_failure = re.search(r'не тормоз|плохо тормоз|хуже тормоз|перегрев|дым|заклини|скрежет|провалива|вибрац|биени', text)
    if 'колод' in name and re.search(r'скрип|свист', text) and not braking_failure:
        return result('pad_squeal', 'Извините, что колодки сильно скрипят. После установки новым колодкам требуется притирка к поверхности диска; скрип также может быть связан с состоянием дисков, креплений или работой суппорта. По отзыву нельзя определить причину, поэтому при сильном скрипе рекомендуем сначала проверить тормозную систему и установку в сервисе; после проверки соблюдать порядок притирки производителя. При ухудшении торможения или перегреве прекратите эксплуатацию до диагностики.', BREMBO, 'Общие рекомендации Brembo: шум может быть связан с дисками, креплениями и суппортом; после установки требуется корректная притирка. Это не диагноз для ADVICS и не обещание исчезновения скрипа.')

    if 'наконечник' in name and re.search(r'рулев|тяги', name) and re.search(r'гайк|шплинт|палец|конус', text) and re.search(r'ниже|выше|тоньше|не совп|болта|люфт', text):
        return result('steering_fastener', 'Извините, что после установки положение гайки и шплинта вызвало сомнения. Если шплинт не фиксирует гайку в предусмотренном положении, эксплуатацию автомобиля следует отложить до проверки в сервисе. Попросите специалиста сверить применяемость наконечника, посадку конуса пальца, комплект крепежа и затяжку с документацией автомобиля. По описанию нельзя подтвердить, что диаметр пальца неверный; самостоятельно подгонять крепление не рекомендуем.', MOOG, 'Общий принцип MOOG: для рулевых наконечников необходим предусмотренный крепеж; это не инструкция по установке детали 555 на конкретный автомобиль.')

    if getattr(review, 'risk', 'NORMAL') != 'NORMAL' or analysis['buyer_asks_about_money']:
        return None
    if analysis.get('sprayer_replaced') and set(analysis['complaints']) & {'LEAKAGE','DAMAGED_ITEM','QUALITY_ISSUE'}:
        praise = 'Рады, что Вы отметили хороший результат очистки. ' if re.search(r'очища\w*\s+хорош|моет\s+хорош|отмыва\w*\s+хорош', text) else ''
        problems = ['поломку распылителя']
        if 'LEAKAGE' in analysis['complaints']: problems.append('протечку')
        if 'PACKAGING_ISSUE' in analysis['complaints']: problems.append('состояние упаковки')
        answer = praise+'Приносим извинения за '+', '.join(problems)+'. Вы уже заменили распылитель, и средство работает. Сожалеем о доставленных неудобствах.'
        return result('replaced_sprayer', answer)
    # A date complaint must not fall through to cleaner usage advice. The owner
    # supplied the Korean YY.MM.DD convention; require the package to confirm it.
    if getattr(product, 'brand', '').strip().upper() == 'KANGAROO' and re.search(r'просроч|годност|дата.*изготов|изготовлено', text):
        explanation = 'У Kangaroo встречается маркировка даты в порядке «год–месяц–день», то есть в обратном порядке к привычному «день–месяц–год». '
        if '26.07.21' in text:
            explanation += 'Если на Вашей упаковке указан формат ГГ.ММ.ДД, запись 26.07.21 означает 21 июля 2026 года, а не 26 июля 2021 года. '
        return result('kangaroo_date', 'Извините, что маркировка даты вызвала сомнения. '+explanation+'Сверьте обозначение формата и срок годности на этикетке; без обозначения формата нельзя подтвердить расшифровку даты.')
    if analysis.get('resolved') and analysis['complaints']:
        from .reply_policy import contextual_complaint_reply
        return {'key': 'resolved_complaint', 'answer': contextual_complaint_reply(analysis), 'sources': []}
    if re.search(r'стеклоочистител|дворник', name) and re.search(r'скрип|шум|плохо чист', text) and not re.search(r'полос|развод|маж[еу]т|мажет|не\s+(?:скрип|шум)', text) and not set(analysis['complaints']) & {'WRONG_ITEM','MISSING_PARTS'}:
        return result('wiper_performance', 'Извините за шум или неудовлетворительную очистку стекла. Проверьте фиксацию щёток, очистите стекло и резиновую кромку мягкой влажной тканью. Не включайте дворники по сухому или обледеневшему стеклу. Если проблема сохраняется, требуется проверка состояния щёток и их установки.', DENSO_WIPERS, 'DENSO: проверить фиксацию щёток, чистоту стекла и резиновой кромки; не использовать щётки по сухому или обледеневшему стеклу.')
    if re.search(r'щетк\w*\s+стеклоочист|стеклоочистител|дворник', name) and re.search(r'полос|развод|маж[еу]т|мажет', text) and not re.search(r'не\s+(?:полос|маж)|без\s+(?:полос|развод)', text):
        return result('wiper_streaks', 'Извините, что щётка начала оставлять полосы. Очистите лобовое стекло средством для автомобильных стёкол, а резиновую кромку аккуратно протрите мягкой тканью с тёплой водой; при необходимости используйте немного мягкого моющего средства и смойте его. Проверьте фиксацию щётки в креплении. Не включайте дворники по сухому или обледеневшему стеклу. Если после очистки полосы остаются, проверьте состояние кромки и установку: при повреждении резины щётку следует заменить. При ухудшении обзора сначала устраните проблему.', DENSO_WIPERS, 'Общие рекомендации DENSO по уходу: очистить стекло и резиновую кромку тёплой водой с небольшим количеством мягкого моющего средства; если полосы сохраняются, заменить щётку. Это не диагноз дефекта Double Force.')
    if quantity_shortage(text) and set(analysis['complaints']) == {'MISSING_PARTS'}:
        return result('quantity_shortage', 'Приносим извинения, что Вы получили меньше единиц товара, чем ожидали. Сверьте количество в заказе и комплектность в карточке товара. Если количество не соответствует заказу, обратитесь в поддержку маркетплейса.')
    if set(analysis['complaints']) == {'AUTHENTICITY'} and getattr(product, 'brand', '').strip().upper() != 'EIKOSHA':
        return result('authenticity_pickup', 'Сожалеем, что подлинность товара вызвала у Вас сомнения. По одному отзыву проверить конкретный экземпляр невозможно. Сверьте маркировку и номер партии на упаковке с данными производителя; при несоответствии обратитесь в поддержку маркетплейса.')
    can_fragrance = getattr(product,'brand','').upper()=='EIKOSHA' and bool(re.search(r'мелов|баноч|air spencer|\ba-?\d+',name)) and not re.search(r'держател|giga|стерж|\bv-?\d+',name)
    if can_fragrance and re.search(r'(?:прислал\w*|пришел|пришла|получил\w*)[^.!?]{0,45}держател',text):
        gift = ' Особенно жаль, что ароматизатор предназначался в подарок и эта ситуация испортила впечатление от покупки.' if 'подар' in text and not re.search(r'не\s+(?:в\s+)?подар',text) else ''
        return result('wrong_fragrance_holder','Приносим извинения за то, что вместо ароматизатора Вам пришёл держатель.'+gift+' Если при получении заказа в ПВЗ Вы обнаружите, что пришёл не тот товар, Вы можете отказаться от его получения.')
    if re.search(r'сломал\w*|отлом\w*|лопнул\w*|треснул\w*',text) and not re.search(r'не\s+(?:слом|отлом|лопнул|треснул)',text) and not set(analysis['complaints'])-{'DAMAGED_ITEM','QUALITY_ISSUE'}:
        detail = 'крепёж держателя сломался' if re.search(r'держател|креплен',name) and re.search(r'крепеж|креплен|защелк|клипс',text) else 'товар сломался'
        return result('broken_item','Извините, что '+detail+' и товар не оправдал Ваших ожиданий. Сожалеем, что Вам пришлось столкнуться с такой поломкой.')

    if re.search(r'\b(?:быушн\w*|бэушн\w*|б\s*/\s*у|бу)\b|использованн|бывш\w*\s+в\s+употреблении|следы\s+(?:использования|установки)', text) and not re.search(r'не\s+(?:использованн|быушн|бэушн|б\s*/\s*у)|без\s+следов', text):
        return result('used_goods', 'Приносим извинения за состояние полученного товара. Мы продаём только новый товар, бывшие в употреблении товары не реализуем. Если при получении в ПВЗ Вы обнаружите следы использования или несоответствие заказу, Вы можете отказаться от получения товара.')

    if analysis['sentiment'] != 'mixed' and not set(analysis['complaints']) - {'DAMAGED_ITEM','PACKAGING_ISSUE','LEAKAGE','WRONG_ITEM'} and re.search(r'помят|поврежд|разбит|протек|вытек|утек|пришел не тот|прислали не тот|не тот товар|получил\w* другой товар', text):
        if re.search(r'вытек|протек|утек', text):
            detail = 'повреждение упаковки и протечку электролита' if 'аккумулятор' in name else 'повреждение упаковки и протечку масла' if 'масл' in name else 'повреждение упаковки и протечку средства'
        elif re.search(r'пришел не тот|прислали не тот|не тот товар|получил\w* другой товар', text):
            detail = 'то, что Вы получили не тот товар'
        else:
            detail = 'состояние упаковки' if set(analysis['complaints']) == {'PACKAGING_ISSUE'} else 'повреждение полученного товара'
        ending = ' Если при получении заказа в ПВЗ обнаружится повреждение или несоответствие заказу, Вы можете отказаться от получения.' if set(analysis['complaints']) & {'LEAKAGE','DAMAGED_ITEM','WRONG_ITEM'} else ''
        return result('pickup_damage', 'Приносим извинения за '+detail+'. Сожалеем, что состояние заказа испортило впечатление от покупки.'+ending)

    if 'галоген' in name and 'ламп' in name and re.search(r'прозрачн\w*\s+стекл|прозрачн\w*\s+колб|где\s+там\s+галоген', text):
        return result('clear_halogen', 'Извините, что свет не оправдал Ваших ожиданий. Прозрачная стеклянная колба для стандартной галогенной лампы — обычное исполнение: «галогенная» означает газовое наполнение внутри колбы, а не цвет стекла. Синее покрытие не является обязательным признаком галогенной лампы; у этой модели в карточке указана цветовая температура 3200 K, то есть тёплый оттенок света.' if '3200' in name else 'Извините, что лампа не оправдала Ваших ожиданий. Прозрачная колба допустима для галогенной лампы: её название относится к газовому наполнению, а не цвету стекла. Синее покрытие не обязательно; оттенок и яркость следует оценивать по характеристикам конкретной модели.', HELLA, 'HELLA: в колбе галогенной лампы находится галогенный газ и вольфрамовая нить; окраска колбы не определяет тип лампы.')

    praise = re.search(r'\b(?:чудесн\w*|прекрасн\w*|отличн\w*|хорош\w*|великолепн\w*|понравил\w*|замечательн\w*|рекомендую|супер)\b', text)
    negative = re.search(r'\bне\b|\bно\b|плох|разочар|недовол|скрип|свист|слом|дефект|жаль|тускл|исчез|пропал|перестал', text)
    if review.rating <= 2 and praise and not negative and not analysis['complaints']:
        detail = 'Рады, что Вам понравился аромат средства.' if re.search(r'аромат|запах', text) else 'Рады, что Вы остались довольны товаром.'
        return result('positive_low_rating', detail+' Вы оставили положительный отзыв, но поставили низкую оценку. Если оценка выбрана случайно, пожалуйста, измените её на ту, которая соответствует Вашему впечатлению от покупки.')
    return None
