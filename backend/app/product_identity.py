"""Flag a conflicting manufacturer mention; never guess or overwrite identity."""
import re
from sqlalchemy import and_, or_, func
from .db import Product

# Only known manufacturers are compared. Vehicle applications and explicit
# "for <brand>" mentions are deliberately not treated as identity evidence.
MANUFACTURERS = ('SOFT99', 'EIKOSHA', 'KANGAROO', 'GIGAS', 'TCL', 'AVANTECH', 'KOITO', 'ADVICS', 'DENSO', 'NGK', 'LERATON')


def identity_warning(product):
    title = (product.name or '').strip()
    for brand in MANUFACTURERS:
        if re.search(r'\b' + re.escape(brand) + r'\b', title, re.I) and not re.search(r'для\s+' + re.escape(brand) + r'\b', title, re.I) and (product.brand or '').strip().upper() not in ('', brand):
            return f'В названии упомянут {brand}, в поле бренда — {product.brand}. Сверьте карточку маркетплейса и источник производителя по артикулу; автоматическая замена не выполнялась.'
    return ''


def identity_conflict_condition():
    title = func.upper(func.trim(Product.name))
    for punctuation in '/-,():':
        title = func.replace(title, punctuation, ' ')
    title = ' ' + title + ' '
    brand = func.upper(func.trim(Product.brand))
    return or_(*(and_(title.contains(' ' + name + ' ', autoescape=True), ~title.contains('для ' + name, autoescape=True), ~title.contains('ДЛЯ ' + name, autoescape=True), brand != '', brand != name) for name in MANUFACTURERS))
