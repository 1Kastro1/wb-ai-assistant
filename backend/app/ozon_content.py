"""Read-only Ozon catalog import; keep SKU identities used by buyer messages."""
import asyncio
import html
import re

from .db import Product, now, setting, set_setting
from .security import guard
from .verified_instructions import attach_verified_instructions


def plain(value):
    return html.unescape(re.sub(r'<[^>]+>', ' ', str(value or ''))).strip()


def store_card(db, store, item, attributes, definitions):
    sku = str(item.get('sku') or attributes.get('sku') or '')
    aliases = {sku} if sku and sku != '0' else set()
    aliases.update(str(source['sku']) for source in item.get('sources') or [] if source.get('sku'))
    if not aliases:
        aliases.add('product:' + str(item['id']))
    values = []
    brand = part = ''
    for attribute in attributes.get('attributes') or []:
        name = definitions.get(attribute.get('id'), '')
        text = '; '.join(plain(v.get('value')) for v in attribute.get('values') or [] if v.get('value'))
        if not name or not text or name.lower() in ('rich-контент', 'rich-контент json'):
            continue
        if name.lower() in ('бренд', 'brand'): brand = text
        if name.lower() in ('артикул производителя', 'партномер', 'part number'): part = text
        values.append((name, text))
    canonical = sku if sku and sku != '0' else sorted(aliases)[0]
    existing = {alias for alias in aliases if db.get(Product, f'ozon:{store.id}:{alias}')}
    for alias in aliases:
        set_setting(db, f'ozon-product:{store.id}:{alias}', f'ozon:{store.id}:{canonical}')
    for alias in sorted(existing | {canonical}):
        pid = f'ozon:{store.id}:{alias}'
        product = db.get(Product, pid) or Product(id=pid, marketplace='ozon')
        product.name = plain(item.get('name') or attributes.get('name') or product.name or alias)
        product.brand = brand or product.brand or ''
        product.part_number = part or product.part_number or ''
        product.category = plain(item.get('type_name') or attributes.get('type_name') or product.category)
        image = item.get('primary_image') or attributes.get('primary_image') or ''
        if isinstance(image, list): image = image[0] if image else ''
        if isinstance(image, str) and image.startswith('https://'): product.image_url = image
        features = [f'{name}: {value}' for name, value in values]
        if attributes.get('weight') is not None:
            features.append('Масса с упаковкой: ' + str(attributes['weight']) + ' ' + str(attributes.get('weight_unit') or ''))
        if all(attributes.get(key) is not None for key in ('depth', 'width', 'height')):
            features.append('Габариты упаковки: ' + ' × '.join(str(attributes[key]) for key in ('depth', 'width', 'height')) + ' ' + str(attributes.get('dimension_unit') or ''))
        features += ['Артикул продавца: ' + str(item.get('offer_id') or attributes.get('offer_id') or ''), 'ID товара Ozon: ' + str(item['id'])]
        if item.get('barcodes'): features.append('Штрихкоды: ' + ', '.join(item['barcodes']))
        facts = [f for f in product.facts or [] if f.get('imported_from') != 'ozon-catalog']
        facts.append({'kind': 'feature', 'text': '\n'.join(features)[:16000], 'source': 'Карточка товара', 'source_label': 'Карточка Ozon', 'verification_status': 'VERIFIED', 'source_type': 'marketplace', 'imported_from': 'ozon-catalog', 'verified_at': now()})
        product.facts = facts
        attach_verified_instructions(db, product)
        db.add(product)


async def sync_catalog(db, store, headers, progress=None, cancelled=None, resume=None, checkpoint=None, between_pages=None):
    cursor = str((resume or {}).get('last_id', ''))
    total = int((resume or {}).get('processed', 0))
    seen = {cursor} if cursor else set()
    category_cache = {}
    category_denied = False
    # Stable common fields returned by product/info/attributes. Category-specific
    # names come from the API; unknown IDs must never be assigned guessed names.
    common_names = {85: 'Бренд', 4191: 'Описание', 7236: 'Артикул производителя'}
    for _ in range(10000):
        if cancelled and cancelled(): raise asyncio.CancelledError()
        result = await guard.request('ozon', 'products', headers=headers, body={'filter': {'visibility': 'ALL'}, 'last_id': cursor, 'limit': 100})
        payload = result.get('result') or {}
        if not isinstance(payload.get('items'), list):
            raise ValueError('Ozon не вернул каталог товаров. Проверьте права API-ключа на чтение товаров.')
        rows = payload['items']
        if not rows: return total
        ids = [str(row['product_id']) for row in rows]
        details = await guard.request('ozon', 'product_info', headers=headers, body={'product_id': ids})
        attrs = await guard.request('ozon', 'product_attributes', headers=headers, body={'filter': {'product_id': ids, 'visibility': 'ALL'}, 'limit': 100})
        cards = details.get('items')
        attributes = attrs.get('result')
        if not isinstance(cards, list) or not isinstance(attributes, list) or {str(c.get('id')) for c in cards} != set(ids) or {str(c.get('id')) for c in attributes} != set(ids):
            raise ValueError('Ozon вернул неполные карточки товаров; прогресс предыдущих страниц сохранён.')
        by_id = {str(a['id']): a for a in attributes}
        for item in cards:
            if cancelled and cancelled(): raise asyncio.CancelledError()
            attr = by_id[str(item['id'])]
            category = (attr.get('description_category_id'), attr.get('type_id'))
            if category not in category_cache:
                names = {'result': []}
                if not category_denied:
                    try:
                        names = await guard.request('ozon', 'category_attributes', headers=headers, body={'description_category_id': category[0], 'type_id': category[1], 'language': 'DEFAULT'})
                    except ValueError as error:
                        if 'HTTP 403' not in str(error): raise
                        category_denied = True
                        set_setting(db, 'ozon-catalog-warning:' + store.id, 'API-ключ не имеет доступа к справочнику категорий. Загружены доступные характеристики; для остальных нужны права на чтение справочников Ozon.')
                if not isinstance(names.get('result'), list): raise ValueError('Ozon не вернул названия характеристик товара')
                category_cache[category] = {**common_names, **{a['id']: a['name'] for a in names['result']}}
                if not category_denied: set_setting(db, 'ozon-catalog-warning:' + store.id, '')
            store_card(db, store, item, attr, category_cache[category])
        db.commit()
        total += len(cards)
        if progress: progress(min(95, max(5, int(total * 95 / max(total, int(payload.get('total') or total))))))
        next_cursor = str(payload.get('last_id') or '')
        if not next_cursor or total >= int(payload.get('total') or 0) > 0: return total
        if next_cursor in seen: raise ValueError('Ozon повторил страницу каталога; продолжите с сохранённого прогресса')
        seen.add(next_cursor)
        cursor = next_cursor
        if checkpoint: checkpoint({'last_id': cursor, 'processed': total})
        if between_pages: await between_pages()
        await asyncio.sleep(0.2)
    raise ValueError('Достигнут лимит страниц каталога Ozon; продолжите с сохранённого прогресса')
