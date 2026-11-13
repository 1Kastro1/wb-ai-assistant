"""Model/card specifications, never measurements of the buyer's lamp."""
import re

AVANTECH_LAMPS = 'https://avantechparts.co.kr/ru/catalog/lamps'


def lamp_specifications(product):
    name = str(getattr(product, 'name', '') or '')
    brand = str(getattr(product, 'brand', '') or '').strip().upper()
    facts = []
    # This manufacturer page describes the series, not a tested individual lamp.
    exact = (brand == 'AVANTECH' and re.search(r'(?<![A-Z0-9])AB5001(?![A-Z0-9])', name, re.I)
             and not re.search(r'\bAB\d{4}\b', re.sub(r'\bAB5001\b', '', name, flags=re.I), re.I)
             and not (set(re.findall(r'AB\d{4}', str(getattr(product,'part_number','') or '').upper()))-{'AB5001'}))
    if exact:
        facts.append({'text': 'Серия Avantech NIGHT FIGHTER: цветовая температура 5000 К, холодный белый свет. '
                      'Кельвины обозначают оттенок света, не потребляемую мощность и не яркость. '
                      'Описание серии не является измерением ламп покупателя.',
                      'source': AVANTECH_LAMPS, 'source_type': 'manufacturer', 'kind': 'specification',
                      'verification_status': 'VERIFIED', 'source_checked_at': '2026-11-13'})
    # Card facts must label the quantity. Never pick a random number from prose,
    # retailer search snippets, "equivalent watts", or another article's facts.
    powers, voltages = set(), set()
    for fact in getattr(product, 'facts', None) or []:
        if fact.get('verification_status') != 'VERIFIED':continue
        source = str(fact.get('source', ''))
        if source != 'Карточка товара' and not source.startswith('Карточка WB:'):continue
        text = str(fact.get('text', ''))
        p = re.search(r'(?:^|\n)\s*(?:Номинальная |Потребляемая )?Мощность(?:\s*,\s*(?:Вт|W))?\s*:\s*(\d+(?:[.,]\d+)?)\s*(?:Вт|W)?\s*(?:\n|$)', text, re.I)
        v = re.search(r'(?:^|\n)\s*(?:Номинальное )?Напряжение(?:\s*,\s*(?:В|V))?\s*:\s*(\d+(?:[.,]\d+)?)\s*(?:В|V)?\s*(?:\n|$)', text, re.I)
        if p:powers.add(format(float(p[1].replace(',','.')),'g'))
        if v:voltages.add(format(float(v[1].replace(',','.')),'g'))
    power = next(iter(powers)) if len(powers)==1 else None
    voltage = next(iter(voltages)) if len(voltages)==1 else None
    conflicting = len(powers)>1 or len(voltages)>1
    # Reviewed AB5001 card notation: 55W is the declared rating, not a bench test.
    if exact and re.search(r'\bH1\b', name, re.I) and re.search(r'(?<!\d)55\s*(?:W|Вт)\b', name, re.I) and re.search(r'(?<!\d)12\s*(?:V|В)\b', name, re.I):
        if powers-{'55'} or voltages-{'12'}:conflicting=True
        if not conflicting:
            power, voltage = '55', '12'
            facts.append({'text': 'В карточке именно Avantech AB5001 указаны H1, 12 В и заявленная мощность 55 Вт. '
                      'Это номинальные характеристики модели, не результат измерения экземпляра покупателя.',
                      'source': 'Карточка товара', 'source_type': 'marketplace', 'kind': 'specification',
                          'verification_status': 'VERIFIED'})
    return {'power': None if conflicting else power, 'voltage':voltage,
            'conflicting':conflicting, 'night_fighter': bool(exact), 'facts': facts}


def power_question_reply(product):
    spec = lamp_specifications(product)
    if spec['conflicting']:
        return ('В характеристиках указаны разные значения мощности или напряжения — эти данные нужно уточнить.',spec['facts'])
    if not spec['power']:
        return ('Чтобы разобраться с вопросом о мощности, сверьте маркировку на лампе с характеристиками в карточке.', spec['facts'])
    answer = f"Для этой модели заявлена мощность {spec['power']} Вт"
    if spec['voltage']:answer += f" при {spec['voltage']} В"
    answer += '.'
    if spec['night_fighter']:
        answer += ' А 5000 К — это холодный белый оттенок света, а не показатель яркости.'
    answer += ' Если заметной разницы в свете нет, это само по себе ещё не означает, что мощность ниже заявленной.'
    return answer, spec['facts']
