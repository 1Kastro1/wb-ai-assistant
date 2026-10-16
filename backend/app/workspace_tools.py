"""Read-only workspace previews and catalog validation."""
import json
import re
from sqlalchemy import select
from .db import Product,StoreListing


def unique_catalog_rows(rows):
    seen=set();result=[]
    for row in rows:
        key=json.dumps(row,sort_keys=True,ensure_ascii=False)
        if key not in seen:seen.add(key);result.append(row)
    return result,len(rows)-len(result)


def parse_mapping(value,fields):
    try:mapping=json.loads(value or '{}')
    except (TypeError,ValueError):raise ValueError('Не удалось прочитать сопоставление столбцов') from None
    if not isinstance(mapping,dict) or len(mapping)>100:raise ValueError('Некорректное сопоставление столбцов')
    if any(not isinstance(k,str) or not isinstance(v,str) or (v and v not in fields) for k,v in mapping.items()):raise ValueError('Выберите допустимые поля для столбцов')
    targets=[v for v in mapping.values() if v]
    if len(set(targets))!=len(targets):raise ValueError('Одно поле нельзя назначить нескольким столбцам')
    return mapping


def preview_cross_catalog(db,raw,filename,store,mapping=None):
    from .fitment_catalog import read_catalog_table,clean_catalog_rows,FIELDS,ALIASES,norm
    rows=read_catalog_table(raw,filename)
    headers=[str(h) for h in rows[0] if h is not None]
    suggested={h:ALIASES.get(h.strip().casefold(),h.strip().casefold()) for h in headers}
    suggested={h:v if v in FIELDS else '' for h,v in suggested.items()}
    effective=mapping if mapping else suggested
    accepted=[];errors=[]
    # Read once; validation never creates an import, VIN salt or product fact.
    products={};listing_ids=set()
    wb_headers=[h for h in headers if effective.get(h)=='wb_nm_id']
    ids=sorted({str(row.get(h,'')).strip() for row in rows for h in wb_headers if row.get(h) not in (None,'')})
    for offset in range(0,len(ids),500):
        chunk=ids[offset:offset+500]
        products.update({p.id:p for p in db.scalars(select(Product).where(Product.marketplace=='wb',Product.id.in_(chunk)))})
        listing_ids.update(db.scalars(select(StoreListing.product_id).where(StoreListing.store_id==store,StoreListing.product_id.in_(chunk))))
    for number,row in enumerate(rows,2):
        try:
            cleaned=clean_catalog_rows([row],effective)[0]
            if cleaned.get('wb_nm_id'):
                product=products.get(cleaned['wb_nm_id'])
                if not product or product.id not in listing_ids:raise ValueError('Артикул WB не найден в выбранном магазине')
                if product.brand and norm(product.brand)!=norm(cleaned['brand']):raise ValueError('Бренд не совпадает с карточкой WB')
                if product.part_number and norm(product.part_number)!=norm(cleaned['part_number']):raise ValueError('Артикул производителя не совпадает с карточкой WB')
            accepted.append(cleaned)
        except ValueError as error:
            errors.append({'row':number,'message':re.sub(r'^Строка 2:\s*','',str(error))})
    unique,duplicates=unique_catalog_rows(accepted)
    return {'headers':headers,'mapping':effective,'fields':list(FIELDS),'sample':unique[:8],
            'total':len(rows),'accepted':len(unique),'duplicates':duplicates,'errors':errors,
            'unmapped':sum(not row.get('wb_nm_id') for row in unique),'can_import':bool(unique) and not errors}
