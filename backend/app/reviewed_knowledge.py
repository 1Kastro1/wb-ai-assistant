"""Reviewed manufacturer material; category guides never establish exact fitment."""
import json,re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from sqlalchemy import select,or_
from .db import Product,Review,StoreListing,StoreProfile,Setting,setting,set_setting,now

@lru_cache(maxsize=1)
def knowledge_catalog():
    return json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))


def product_scope(store):
    if not store:return Product.id=='__no_store__'
    if store.provider!='wb':return Product.id.startswith(f'{store.provider}:{store.id}:')
    listed=select(StoreListing.product_id).where(StoreListing.store_id==store.id,StoreListing.product_id==Product.id).exists()
    historical=select(Review.id).where(Review.wb_account_id==store.id,Review.product_id==Product.id).exists()
    return (Product.marketplace=='wb') & or_(listed,historical)


def product_owned(db,store,product):
    return bool(product and db.scalar(select(Product.id).where(Product.id==product.id,product_scope(store))))


def matching_knowledge(product):
    from .verified_instructions import manufacturer_articles
    brand=str(product.brand or '').strip().upper()
    articles=manufacturer_articles(product)
    name=str(product.name or '')
    if re.search(r'набор\s+(?:для|средств)|комплект\s+(?:для|инструмент)',name,re.I):return []
    result=[]
    for entry in knowledge_catalog():
        if entry['brand']!=brand:continue
        if entry['scope']=='article':
            if len(articles)!=1 or entry['part_number'].upper() not in articles:continue
        elif not re.search(entry['name_pattern'],name,re.I):continue
        result.append(entry)
    return result


def domain_cache(db):
    return {row.key.removeprefix('official_domain:'):str(row.value or '').lower()
            for row in db.scalars(select(Setting).where(Setting.key.startswith('official_domain:')))}


def attach_reviewed_knowledge(db,product,domains=None):
    entries=matching_knowledge(product)
    if not entries:return 0
    current=(domains.get(product.id,'') if domains is not None else str(setting(db,'official_domain:'+product.id,'') or '').lower())
    facts=list(product.facts or []);added=0
    for entry in entries:
        host=urlsplit(entry['url']).hostname
        if current and host!=current and not host.endswith('.'+current):continue
        if not current:
            set_setting(db,'official_domain:'+product.id,host);current=host
            if domains is not None:domains[product.id]=host
        if any(f.get('catalog_knowledge')==entry['id'] and f.get('text')==entry['text'] and f.get('source')==entry['url'] and f.get('source_checked_at')==entry['checked_at'] and f.get('verification_status')=='VERIFIED' for f in facts):continue
        facts=[f for f in facts if f.get('catalog_knowledge')!=entry['id']]
        facts.append({'text':entry['text'],'source':entry['url'],'kind':entry['kind'],
            'verification_status':'VERIFIED','source_type':'manufacturer','catalog_knowledge':entry['id'],
            'applicability_scope':entry['scope'],'manufacturer_article':entry.get('part_number',''),
            'source_checked_at':entry['checked_at'],'verified_at':now()})
        added+=1
    if added:product.facts=facts
    return added


def knowledge_report(db,store):
    domains=domain_cache(db)
    result=dict(total=0,with_features=0,with_instructions=0,exact_instructions=0,general_instructions=0,missing_instructions=0,missing_articles=0)
    from .verified_instructions import manufacturer_articles
    for product in db.scalars(select(Product).where(product_scope(store)).execution_options(yield_per=500)):
        result['total']+=1
        if not manufacturer_articles(product):result['missing_articles']+=1
        features=False;exact=False;general=False
        domain=domains.get(product.id,'')
        for fact in product.facts or []:
            if fact.get('verification_status')!='VERIFIED':continue
            source=str(fact.get('source',''))
            try:url=urlsplit(source);host=url.hostname or ''
            except ValueError:continue
            trusted=source=='Карточка товара' or source.startswith('Карточка WB:') or url.scheme=='https' and bool(domain) and (host==domain or host.endswith('.'+domain))
            if not trusted:continue
            if fact.get('kind')=='instruction':
                if fact.get('applicability_scope')=='brand_category':general=True
                else:exact=True
            elif fact.get('kind')=='feature' or source=='Карточка WB: характеристики':features=True
        result['with_features']+=features;result['exact_instructions']+=exact;result['general_instructions']+=general and not exact
        result['with_instructions']+=exact or general
    result['missing_instructions']=result['total']-result['with_instructions']
    return result


def refresh_reviewed_knowledge(db,store):
    from .verified_instructions import attach_verified_instructions
    domains=domain_cache(db);changed=0;facts=0
    for product in db.scalars(select(Product).where(product_scope(store)).execution_options(yield_per=300)):
        count=attach_verified_instructions(db,product,known_domains=domains)
        if count:changed+=1;facts+=count
    db.commit()
    return {'changed_products':changed,'added_facts':facts,**knowledge_report(db,store)}
