"""Answer accessory and fragrance questions from the active store's catalog."""
import re
from sqlalchemy import select
from .db import Product, StoreListing, StoreProfile


def store_products(db, store_id):
    store = db.get(StoreProfile, store_id)
    query = select(Product).where(Product.brand.ilike('EIKOSHA'))
    if store and store.provider != 'wb':
        query = query.where(Product.marketplace == store.provider, Product.id.startswith(f'{store.provider}:{store_id}:'))
    else:
        query = query.join(StoreListing, StoreListing.product_id == Product.id).where(StoreListing.store_id == store_id, Product.marketplace == 'wb')
    return list(db.scalars(query.order_by(Product.id)))


def catalog_reference(product):
    if product.marketplace == 'ozon':
        sku = product.id.rsplit(':', 1)[-1]
        if not sku.isdigit() or ':product:' in product.id: return None
        return 'артикул Ozon ' + sku, 'https://www.ozon.ru/product/' + sku + '/'
    if product.marketplace == 'yandex':
        return 'артикул в нашем каталоге Яндекс Маркета ' + product.id.rsplit(':', 1)[-1], 'Карточка товара'
    if re.fullmatch(r'[1-9][0-9]{0,11}', product.id):
        return 'артикул Wildberries ' + product.id, 'https://www.wildberries.ru/catalog/' + product.id + '/detail.aspx'
    return None

SCENTS = (
    ('яблочный', r'ябл|\bapple\b'),
    ('лимонный', r'лимон|\blemon\b'),
    ('ванильный', r'ванил|\bvanilla\b'),
    ('цитрусовый', r'цитрус|\bcitrus\b'),
    ('вишнёвый', r'вишн|\bcherry\b'),
    ('персиковый', r'персик|\bpeach\b'),
    ('мускусный', r'мускус|\bmusk\b'),
)
MOUNT = r'держател|креплен|закрепител|как.*(?:закреп|крепить|установ)|где.*(?:размест|постав|располож)|куда.*(?:постав|креп|установ)'


def fragrance_format(product):
    name = product.name.lower()
    if re.search(r'картридж|стерж|giga|\bv-?\d+', name):
        return 'giga'
    if re.search(r'баноч|меловой ароматизатор|\ba-?\d+', name):
        return 'can'
    return 'unknown'


def standalone_holder(product):
    # A fragrance bundle that happens to include a holder is not an accessory
    # listing. Ambiguous titles opt out instead of recommending the wrong SKU.
    name = product.name.strip()
    return bool(re.search(r'^(?:EIKOSHA\s+)?(?:держатель|крепление)\b', name, re.I)
                and not re.search(r'набор|комплект|\+', name, re.I))


def assortment_question_answer(db, question, product, facts_for_product):
    if product.brand.strip().casefold() != 'eikosha':
        return None
    text = (question.text or '').lower().replace('ё', 'е')
    mount = bool(re.search(MOUNT, text))
    scents = [(label, pattern) for label, pattern in SCENTS if re.search(pattern, text, re.I)]
    if not mount and not scents:
        return None
    # A question about included equipment needs the current card's package contents.
    if mount and re.search(r'входит|в комплекте|комплектуется', text) and not re.search(r'отдельно|купить', text):
        return None
    if scents and re.search(r'без\s+(?:ябл|лимон|ванил|цитрус|вишн|персик|мускус)|не\s+(?:хочу|люблю|нужен)', text):
        return None
    products = [p for p in store_products(db, question.wb_account_id) if catalog_reference(p)]
    sources, selected = [], []

    def evidence(p):
        if p.id not in selected:
            selected.append(p.id)
            reference, source = catalog_reference(p)
            sources.append({'text': p.brand+' '+p.name+' · '+reference,
                            'source': source,
                            'verification_status': 'VERIFIED'})

    def result(answer, intent):
        return {'answer': answer, 'sources': sources, 'product_ids': selected, 'intent': intent}

    if mount:
        if fragrance_format(product) != 'can':
            return None  # Can holders are not holders for the GIGA refill sticks.
        holders = []
        for p in products:
            if not standalone_holder(p):
                continue
            facts = facts_for_product(db, p)
            description = p.name+' '+' '.join(f.get('text', '') for f in facts)
            if re.search(r'остаток\s*:\s*0\b|нет в наличии', description, re.I):
                continue
            if re.search(r'GIGA|стержн', description, re.I) and not re.search(r'баноч|мелов', description, re.I):continue
            holders.append((p, description))
        vent = next((p for p, desc in sorted(holders, key=lambda item: (not bool(re.search('дефлектор', item[0].name, re.I)), item[0].id)) if re.search(r'дефлектор|решетк|решётк|вентиляционн', desc, re.I)), None)
        panel = next((p for p, desc in holders if re.search(r'nonslip|нескольз', p.name, re.I) or re.search(r'(?:модель|место крепления|назначение)\s*:\s*(?:на\s+)?(?:торпед|панел|горизонтальн)', desc, re.I)), None)
        parts = ['Добрый день!']
        if vent:
            evidence(vent)
            parts.append('Для баночки EIKOSHA есть держатель на дефлектор «'+vent.name+'», '+catalog_reference(vent)[0]+'.')
        if panel:
            evidence(panel)
            parts.append('Для устойчивого размещения на горизонтальной поверхности есть «'+panel.name+'», '+catalog_reference(panel)[0]+'; перед размещением на торпеде сверьтесь с инструкцией держателя.')
        parts.append('Баночку ароматизатора также можно разместить в подходящем подстаканнике или кармане двери.')
        if vent:
            parts.append('Для Kia Optima перед выбором крепления на дефлектор сверьте форму и толщину ламелей с требованиями держателя.' if re.search(r'optima|оптим', text) else 'Перед выбором крепления на дефлектор сверьте форму и толщину ламелей с требованиями держателя.')
        sources.append({'text': 'Размещение баночного ароматизатора: подстаканник или карман двери; не ставить свободно на торпеду.',
                        'source': 'https://www.airspencer.com/faq/faq-297/', 'verification_status': 'VERIFIED'})
        return result(' '.join(parts), 'крепление')

    if len(scents) != 1:
        return result('Добрый день! Уточните, пожалуйста, какой аромат Вы ищете и нужен ли Вам ароматизатор в баночке или сменные стержни GIGA.', 'ассортимент')
    label, pattern = scents[0]
    matches = [p for p in products if re.search(pattern, p.name, re.I)
               and not re.search(r'набор|\+|ассорти|держател|креплен', p.name, re.I)]
    matches = [p for p in matches if not any(re.search(r'остаток\s*:\s*0\b|нет в наличии', f.get('text', ''), re.I) for f in facts_for_product(db, p))]
    if not matches:
        return result('Добрый день! В загруженном каталоге нашего магазина не нашёл '+label+' аромат. Уточните, пожалуйста, нужен ли Вам вариант в баночке или сменные стержни GIGA.', 'ассортимент')
    current_format = fragrance_format(product)
    chosen = sorted(matches, key=lambda p: (fragrance_format(p) != current_format, p.id))[0]
    evidence(chosen)
    answer = 'Добрый день! В нашем каталоге есть '+label+' аромат — EIKOSHA «'+chosen.name+'», '+catalog_reference(chosen)[0]+'.'
    chosen_format = fragrance_format(chosen)
    if current_format == 'giga' and chosen_format == 'can':
        answer += ' Это отдельный ароматизатор в баночке, а не сменные стержни для GIGA.'
    elif current_format == 'can' and chosen_format == 'giga':
        answer += ' Это сменные стержни для держателя GIGA; для баночки они не подходят.'
    return result(answer, 'ассортимент')


def review_vent_holder(db,review,product):
    if product.brand.strip().casefold()!='eikosha' or fragrance_format(product)!='can':return None
    holders=store_products(db, review.wb_account_id)
    for holder in holders:
        if not standalone_holder(holder) or not re.search(r'дефлектор',holder.name,re.I) or re.search(r'giga|стерж',holder.name,re.I) or not catalog_reference(holder):continue
        reference, source = catalog_reference(holder)
        return {'text':holder.name+' · '+reference,'kind':'vent_holder','wb_nm_id':holder.id,'article_label':reference,'verification_status':'VERIFIED','source':source}
    return None
