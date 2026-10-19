"""Shared catalog search, including manufacturer articles with common separators."""
from sqlalchemy import func, or_
from .db import Product


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
