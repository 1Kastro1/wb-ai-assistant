"""Specific owner-approved replies; technical statements remain conditional."""
import re
from .reply_policy import analyze_customer_message

HELLA = 'https://www.hella.com/partnerworld/us/Product-range/Lighting/Bulbs-3277/'
BREMBO = 'https://asia.bremboparts.com/america/en/support/car/maintenance/noises-and-vibrations-113941'
MOOG = 'https://www.moogparts.com/technical/bulletins/tech-tips/using-proper-stud-fasteners-for-ball-joint-tie-rod-end-repairs.html'


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
    if re.search(r'\b(?:быушн\w*|бэушн\w*|б\s*/\s*у|бу)\b|использованн|бывш\w*\s+в\s+употреблении|следы\s+(?:использования|установки)', text) and not re.search(r'не\s+(?:использованн|быушн|бэушн|б\s*/\s*у)|без\s+следов', text):
        return result('used_goods', 'Приносим извинения за состояние полученного товара. Мы продаём только новый товар, бывшие в употреблении товары не реализуем. Если при получении в ПВЗ Вы обнаружите следы использования или несоответствие заказу, Вы можете отказаться от получения товара.')

    if analysis['sentiment'] != 'mixed' and not set(analysis['complaints']) - {'DAMAGED_ITEM','PACKAGING_ISSUE','LEAKAGE','WRONG_ITEM'} and re.search(r'помят|поврежд|разбит|протек|вытек|утек|пришел не тот|прислали не тот|не тот товар', text):
        if re.search(r'вытек|протек|утек', text):
            detail = 'повреждение упаковки и протечку масла' if 'масл' in name else 'повреждение упаковки и протечку средства'
        elif re.search(r'пришел не тот|прислали не тот|не тот товар', text):
            detail = 'то, что Вы получили не тот товар'
        else:
            detail = 'повреждение полученного товара'
        return result('pickup_damage', 'Приносим извинения за '+detail+'. Если при получении заказа в ПВЗ товар повреждён, испорчен или не соответствует заказанному, Вы можете отказаться от его получения.')

    if 'галоген' in name and 'ламп' in name and re.search(r'прозрачн\w*\s+стекл|прозрачн\w*\s+колб|где\s+там\s+галоген', text):
        return result('clear_halogen', 'Извините, что свет не оправдал Ваших ожиданий. Прозрачная стеклянная колба для стандартной галогенной лампы — обычное исполнение: «галогенная» означает газовое наполнение внутри колбы, а не цвет стекла. Синее покрытие не является обязательным признаком галогенной лампы; у этой модели в карточке указана цветовая температура 3200 K, то есть тёплый оттенок света.' if '3200' in name else 'Извините, что лампа не оправдала Ваших ожиданий. Прозрачная колба допустима для галогенной лампы: её название относится к газовому наполнению, а не цвету стекла. Синее покрытие не обязательно; оттенок и яркость следует оценивать по характеристикам конкретной модели.', HELLA, 'HELLA: в колбе галогенной лампы находится галогенный газ и вольфрамовая нить; окраска колбы не определяет тип лампы.')

    praise = re.search(r'\b(?:чудесн\w*|прекрасн\w*|отличн\w*|хорош\w*|великолепн\w*|понравил\w*|замечательн\w*|рекомендую|супер)\b', text)
    negative = re.search(r'\bне\b|\bно\b|плох|разочар|недовол|скрип|свист|слом|дефект|жаль|тускл|исчез|пропал|перестал', text)
    if review.rating <= 2 and praise and not negative and not analysis['complaints']:
        detail = 'Рады, что Вам понравился аромат средства.' if re.search(r'аромат|запах', text) else 'Рады, что Вы остались довольны товаром.'
        return result('positive_low_rating', detail+' Вы оставили положительный отзыв, но поставили низкую оценку. Если оценка выбрана случайно, пожалуйста, измените её на ту, которая соответствует Вашему впечатлению от покупки.')
    return None
