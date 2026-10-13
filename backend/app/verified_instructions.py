"""Reviewed product instructions, matched by exact manufacturer article."""
import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit


@lru_cache(maxsize=1)
def instruction_catalog():
    return json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))


def manufacturer_articles(product):
    result = {str(getattr(product, 'part_number', '') or '').strip().upper()}
    for fact in getattr(product, 'facts', None) or []:
        if fact.get('verification_status') == 'VERIFIED' and str(fact.get('source', '')).startswith('Карточка WB:'):
            for match in re.finditer(r'(?:^|\n)Артикул производителя\s*:\s*([^\n]+)', str(fact.get('text', '')), re.I):
                result.add(match.group(1).strip().upper())
    return result - {''}


def matching_instructions(product):
    articles = manufacturer_articles(product)
    if str(getattr(product, 'brand', '')).strip().upper() == 'EIKOSHA':
        articles = {re.sub(r'^A-', 'A', article) for article in articles}
    # A mixed kit needs separate component instructions, not one member's recipe.
    if len(articles) != 1 or 'набор' in getattr(product, 'name', '').lower():
        return []
    brand = str(getattr(product, 'brand', '')).strip().upper()
    return [item for item in instruction_catalog() if item['brand'] == brand and item['part_number'].upper() in articles]


def attach_verified_instructions(db, product):
    from .db import now, setting, set_setting
    items = matching_instructions(product)
    added = 0
    from .steam_deo_guidance import reviewed_steam_facts
    facts = list(product.facts or [])
    for fact in reviewed_steam_facts(product):
        if fact not in facts:
            facts.append(fact)
            added += 1
    if added:
        product.facts = facts
    for item in items:
        host = urlsplit(item['url']).hostname
        key = 'official_domain:' + product.id
        existing_domain = str(setting(db, key, '') or '').lower()
        if existing_domain and existing_domain != host and not host.endswith('.' + existing_domain):
            continue
        if not existing_domain:
            set_setting(db, key, host)
        facts = list(product.facts or [])
        if any(f.get('source') == item['url'] and f.get('text') == item['text'] and f.get('verification_status') == 'VERIFIED' for f in facts):
            continue
        # Preserve user facts and descriptions; update only this reviewed instruction.
        facts = [f for f in facts if not (f.get('source') == item['url'] and f.get('catalog_instruction'))]
        facts.append({'text': item['text'], 'source': item['url'], 'kind': 'instruction',
                      'verification_status': 'VERIFIED', 'source_type': 'manufacturer',
                      'catalog_instruction': True, 'manufacturer_article': item['part_number'],
                      'source_checked_at': item['checked_at'], 'verified_at': now(),
                      'video_url': item.get('video_url', '')})
        product.facts = facts
        added += 1
    return added


def prefer_manufacturer_instructions(facts):
    """Prevent seller usage text from overriding a reviewed manufacturer recipe."""
    official = [f for f in facts if f.get('kind') == 'instruction' and f.get('source_type') == 'manufacturer']
    if not official:
        return facts
    result = []
    for fact in facts:
        if fact in official:
            continue
        copy = dict(fact)
        copy['text'] = re.split(r'(?:способ применения|инструкция по применению|как применять)\s*:', str(copy.get('text', '')), maxsplit=1, flags=re.I)[0].strip()
        if copy['text'] and copy.get('kind') != 'instruction':
            result.append(copy)
    return official + result
