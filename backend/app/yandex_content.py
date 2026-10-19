"""Read categorical card data; never infer names or units from parameter IDs."""
import asyncio
from .security import guard
from .db import now


def parameter_lines(values, parameters):
    definitions = {str(p['id']): p for p in parameters if isinstance(p, dict) and p.get('id') is not None}
    lines = []
    for item in values:
        definition = definitions.get(str(item.get('parameterId')))
        if not definition or not definition.get('name'):
            continue
        value = item.get('value')
        if value is None or value == '':
            value = next((v.get('value') for v in definition.get('values') or []
                          if str(v.get('id')) == str(item.get('valueId'))), None)
        if value is None or value == '':
            continue
        if isinstance(value, bool):
            value = 'Да' if value else 'Нет'
        elif definition.get('type') == 'BOOLEAN' and str(value).lower() in ('true', 'false'):
            value = 'Да' if str(value).lower() == 'true' else 'Нет'
        unit_id = item.get('unitId')
        if unit_id is None and definition.get('type') == 'NUMERIC':
            unit_id = (definition.get('unit') or {}).get('defaultUnitId')
        unit = ''
        if unit_id is not None:
            unit = next((str(u.get('name') or u.get('fullName') or '')
                         for u in (definition.get('unit') or {}).get('units') or []
                         if str(u.get('id')) == str(unit_id)), '')
            # A number without its declared unit could silently mislead the buyer.
            if not unit:
                continue
        line = f"{definition['name']}: {value}" + (f' {unit}' if unit else '')
        if line not in lines:
            lines.append(line)
    return lines


async def card_characteristics(store, token, offers, category_cache, cancelled=None, warnings=None):
    """At most 100 offers from the current page; category schemas cached per import."""
    eligible = {str((item.get('offer') or {}).get('offerId')): item for item in offers
                if (item.get('mapping') or {}).get('marketCategoryId')}
    if not eligible:
        return {}
    business_id = str(store.config['business_id']).strip()
    if cancelled and cancelled():
        raise asyncio.CancelledError()
    await asyncio.sleep(0.65)
    try:
        response = await guard.request('yandex', 'product_cards', headers={'Api-Key': token},
            url=f'https://api.partner.market.yandex.ru/v2/businesses/{business_id}/offer-cards',
            params={'limit': 200}, body={'offerIds': list(eligible)})
    except ValueError as error:
        if not any(f'HTTP {code}' in str(error) for code in (403, 429, 500, 502, 503, 504)): raise
        # Detailed cards are supplemental; the primary offer-mappings response
        # still supplies authoritative product identities and base properties.
        warning = 'подробные карточки, начиная с ' + next(iter(eligible))
        if warnings is not None and warning not in warnings: warnings.append(warning)
        return {}
    payload = response.get('result')
    if response.get('status') == 'ERROR' or not isinstance(payload, dict) or not isinstance(payload.get('offerCards'), list):
        raise ValueError('Яндекс Маркет не вернул подробные характеристики карточек')
    if (payload.get('paging') or {}).get('nextPageToken'):
        raise ValueError('Яндекс Маркет вернул неполный набор характеристик; повторите синхронизацию')
    result = {}
    for card in payload['offerCards']:
        sku = str(card.get('offerId') or '')
        if sku not in eligible:
            continue
        values = card.get('parameterValues') or []
        if not values:
            result[sku] = []
            continue
        category = (card.get('mapping') or {}).get('marketCategoryId') or (eligible[sku].get('mapping') or {}).get('marketCategoryId')
        if not str(category or '').isdigit() or int(category) <= 0:
            continue
        category = str(category)
        if category not in category_cache:
            if cancelled and cancelled():
                raise asyncio.CancelledError()
            await asyncio.sleep(0.65)
            try:
                schema = await guard.request('yandex', 'category_parameters', headers={'Api-Key': token},
                    url=f'https://api.partner.market.yandex.ru/v2/category/{category}/parameters',
                    params={'businessId': int(business_id)})
            except ValueError as error:
                # Optional category dictionaries must not block the base catalog.
                # None keeps existing detailed facts instead of replacing them.
                if not any(f'HTTP {code}' in str(error) for code in (403, 429, 500, 502, 503, 504)): raise
                category_cache[category] = None
                if warnings is not None and category not in warnings: warnings.append(category)
                continue
            data = schema.get('result')
            if schema.get('status') == 'ERROR' or not isinstance(data, dict) or not isinstance(data.get('parameters'), list):
                raise ValueError('Не удалось получить названия характеристик категории Яндекс Маркета')
            category_cache[category] = data['parameters']
        if category_cache[category] is None: continue
        lines = parameter_lines(values, category_cache[category])
        result[sku] = [{'text': '\n'.join(lines[start:start + 30]), 'source': 'Карточка товара',
                        'source_label': 'Яндекс Маркет · подробные характеристики',
                        'kind': 'feature', 'verification_status': 'VERIFIED', 'source_type': 'marketplace',
                        'imported_from': 'yandex-parameters', 'verified_at': now()}
                       for start in range(0, len(lines), 30)]
    return result
