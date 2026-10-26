"""Shared catalog search, including manufacturer articles with common separators."""
from sqlalchemy import func, or_, select, literal, cast, Text, and_
from .db import Product, Review, Setting


ARTICLE_SEPARATORS = ' -‐‑‒–—−./_\t\n\r\u00a0'


def product_search_condition(query):
    query = query.strip()
    conditions = [column.contains(query, autoescape=True) for column in
                  (Product.name, Product.brand, Product.category, Product.part_number)]
    article = query.upper().translate(str.maketrans('', '', ARTICLE_SEPARATORS))
    if article:
        normalized = func.upper(func.coalesce(Product.part_number, ''))
        for separator in ARTICLE_SEPARATORS:
            normalized = func.replace(normalized, separator, '')
        conditions.append(normalized.contains(article, autoescape=True))
    return or_(*conditions)


def knowledge_condition(db):
    """Filter the complete catalog in SQL with the same trust rules as the UI."""
    domain = func.lower(func.coalesce(select(cast(Setting.value, Text)).where(
        Setting.key == literal('official_domain:') + Product.id).correlate(Product).scalar_subquery(), ''))
    # Settings JSON strings include quotes when cast on SQLite/PostgreSQL.
    domain = func.replace(domain, '"', '')
    if db.bind.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import JSONB
        facts = func.jsonb_array_elements(cast(Product.facts, JSONB)).table_valued('value').alias('instruction_facts')
        field = lambda key: facts.c.value.op('->>')(key)
    else:
        facts = func.json_each(Product.facts).table_valued('value').alias('instruction_facts')
        field = lambda key: func.json_extract(facts.c.value, '$.' + key)
    source = field('source')
    tail = func.substr(source, 9)
    if db.bind.dialect.name == 'postgresql':
        host = func.lower(func.split_part(func.split_part(tail, '/', 1), ':', 1))
    else:
        authority = func.substr(tail, 1, func.instr(tail + '/', '/') - 1)
        host = func.lower(func.substr(authority, 1, func.instr(authority + ':', ':') - 1))
    trusted = or_(source == 'Карточка товара', source.startswith('Карточка WB:'),
        and_(domain != '', source.startswith('https://'), or_(host == domain, host.endswith(literal('.') + domain))),
        and_(cast(field('reviewed_catalog'), Text).in_(('true', '1')), field('manufacturer_article') == 'D38RU',
             or_(func.coalesce(Product.part_number, '') == '', func.upper(Product.part_number) == 'D38RU'),
             source == 'https://leraton.ru/katalog/cleaners/osvezhiteli-vozduxa-2/dymovye-shashki/ustranitel-nepriyatnyx-zapaxov-deodorant-steam-type-ag-dymovaya-shashka-d38ru-40ml'))
    ready = select(literal(1)).select_from(facts).where(field('kind') == 'instruction',
        field('verification_status') == 'VERIFIED', trusted).correlate(Product).exists()
    conflict = select(literal(1)).select_from(facts).where(or_(field('verification_status') == 'CONFLICT',
        ~func.coalesce(cast(field('conflict'), Text), '').in_(('', 'false', '0', 'null')))).correlate(Product).exists()
    return ready & ~conflict


def priority_products(store_id):
    return select(Review.product_id).where(Review.wb_account_id == store_id, Review.is_answered == False,
        or_(Review.marketplace.like('%_question'), Review.rating <= 3)).distinct()
