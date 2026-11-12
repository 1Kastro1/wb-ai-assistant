"""Identical SQL keys for catalog lookup queries and their indexes."""
from sqlalchemy import func


def brand_lookup_key(column):
    return func.lower(func.trim(column))


def article_lookup_key(column):
    value=column
    for separator in ('-', ' ', '.', '/', '_'):
        value=func.replace(value,separator,'')
    return func.upper(value)
