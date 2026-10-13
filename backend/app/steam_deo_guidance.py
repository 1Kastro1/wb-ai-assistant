"""Reviewed D38RU seller instructions; never transfer them to another model."""
from .verified_instructions import manufacturer_articles

SOURCE = 'https://leraton.ru/katalog/cleaners/osvezhiteli-vozduxa-2/dymovye-shashki/ustranitel-nepriyatnyx-zapaxov-deodorant-steam-type-ag-dymovaya-shashka-d38ru-40ml'
INSTRUCTION = ('На открытом воздухе уберите из салона людей, животных и продукты, закройте окна; '
               'включите кондиционер на минимальную температуру, максимальный обдув «в лицо» и рециркуляцию на 5 минут. '
               'Переверните коробку, откройте отверстие по прорезям в её нижней части, нажмите PUSH и поставьте коробку на пол перед пассажирским сиденьем, не наклоняя; выйдите и закройте двери. '
               'Выдержите 10 минут, выключите двигатель и проветрите салон не менее 15 минут; банку уберите после остывания.')
EXPLANATION = ('Видимость пара зависит от температуры, влажности и освещения; густой пар не требуется. '
               'Запах нейтрализует активный состав, поэтому малозаметный пар сам по себе не означает отсутствия эффекта.')


def reviewed_steam_facts(product):
    brand = str(getattr(product, 'brand', '')).upper().replace(' ', '')
    if brand not in ('STEAMDEO', 'CARMATE') or manufacturer_articles(product) != {'D38RU'} or 'набор' in product.name.lower():
        return []
    return [dict(text=text, source=SOURCE, kind=kind, verification_status='VERIFIED', source_type='supplier',
                 manufacturer_article='D38RU', source_checked_at='2026-10-13', reviewed_catalog=True)
            for text,kind in ((INSTRUCTION,'instruction'),(EXPLANATION,'description'))]


def steam_deo_reply(review, product, revision=0):
    if not reviewed_steam_facts(product):
        return None
    from .reply_policy import analyze_customer_message
    analysis = analyze_customer_message(review.text, review.rating)
    if review.rating > 3 or set(analysis['complaints']) - {'NO_RESULT'}:
        return None
    text = review.text.lower().replace('ё', 'е')
    if not any(x in text for x in ('дым', 'пар')):
        return None
    thanks = ['Спасибо за отзыв.', 'Благодарим за обратную связь.', 'Спасибо, что поделились опытом.'][revision % 3]
    intro = 'Извините, что при нагреве банки Вы не увидели пара.' if 'нагре' in text else 'Извините, что обработка не оправдала Ваших ожиданий.'
    followed = any(x in text for x in ('по инструкц', 'согласно инструкц'))
    prefix = 'Вы уже следовали инструкции; для сверки: ' if followed else 'Порядок применения: '
    ending = (' Если запах остался, обратитесь в поддержку с описанием обработки.'
              if any(x in text for x in ('никакого эффекта', 'нет эффекта', 'не помог', 'запах остал', 'деньги на ветер')) else '')
    return 'Здравствуйте! ' + thanks + ' ' + intro + ' ' + EXPLANATION + ' ' + prefix + INSTRUCTION + ' Использованную банку повторно не запускайте.' + ending
