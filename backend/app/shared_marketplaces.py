"""Shared reply experience and marketplace-neutral product identity."""
import re
from sqlalchemy import select, func, or_, and_, String
from .db import Product, StoreProfile, StoreListing, Setting, setting
from .verified_instructions import manufacturer_articles

POLICY_VERSION = 'all-marketplaces-1'


def article_key(value):
    return re.sub(r'[^A-ZА-ЯЁ0-9]', '', str(value or '').upper())


def product_identity(product):
    if not product or not str(product.brand or '').strip():
        return None
    if re.search(r'набор|комплект|ассорти', getattr(product, 'name', '') or '', re.I):
        return None
    articles = {article_key(value) for value in manufacturer_articles(product)} - {''}
    if len(articles) != 1:
        return None
    return str(product.brand).strip().casefold(), next(iter(articles))


def equivalent_products(db, product):
    identity = product_identity(product)
    if not identity:
        return [product] if product else []
    raw_articles = manufacturer_articles(product)
    normalized = Product.part_number
    for separator in ('-', ' ', '.', '/', '_'):
        normalized = func.replace(normalized, separator, '')
    predicates = [func.upper(normalized) == identity[1]]
    for article in {*raw_articles, identity[1]}:
        predicates.append(and_(Product.part_number == '', Product.facts.cast(String).contains(article)))
    candidates = db.scalars(select(Product).where(
        func.lower(func.trim(Product.brand)) == identity[0], or_(*predicates)))
    return [candidate for candidate in candidates if product_identity(candidate) == identity]


def shared_counters(db, prefix):
    result = {}
    def add(target, source):
        for key, value in source.items():
            if isinstance(value, dict):
                add(target.setdefault(key, {}), value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                target[key] = target.get(key, 0) + value
    for row in db.scalars(select(Setting).where(Setting.key.startswith(prefix))):
        if isinstance(row.value, dict):
            add(result, row.value)
    return result


def internet_fitment_enabled(db, store_id):
    shared = setting(db, 'internet-fitment-enabled:shared', None)
    if shared is not None:
        return bool(shared)
    # Existing WB preference becomes the default for all connected marketplaces.
    wb_ids = list(db.scalars(select(StoreProfile.id).where(StoreProfile.provider == 'wb')))
    return any(bool(setting(db, 'internet-fitment-enabled:' + sid, False)) for sid in set([store_id, 'owner', *wb_ids]))


def listing_owned(db, store_id, product):
    if not product:
        return False
    store = db.get(StoreProfile, store_id)
    provider = store.provider if store else 'wb'
    if product.marketplace != provider:
        return False
    if provider == 'wb':
        return bool(db.get(StoreListing, (store_id, product.id)))
    return product.id.startswith(provider + ':' + store_id + ':')


def resolve_listing(db, store_id, reference, current_product_id=''):
    reference = str(reference or '').strip()
    if not reference:
        return None
    store = db.get(StoreProfile, store_id)
    provider = store.provider if store else 'wb'
    ids = [reference] if provider == 'wb' or reference.startswith(provider + ':' + store_id + ':') else [provider + ':' + store_id + ':' + reference]
    if provider == 'ozon':
        ids += [setting(db, 'ozon-product:' + store_id + ':' + reference, '')]
    for pid in ids:
        product = db.get(Product, pid) if pid else None
        if product and (listing_owned(db, store_id, product) or product.id == current_product_id):
            return product
    return None


def row_product_id(row):
    return row.get('product_id') or row.get('wb_nm_id') or ''


def product_reference(product):
    from .assortment_questions import catalog_reference
    label, url = catalog_reference(product) or ('артикул нашего каталога ' + product.id.rsplit(':', 1)[-1], '')
    return label, url if url.startswith('https://') else ''

