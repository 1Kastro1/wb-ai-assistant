"""Explicit owner preferences shared by rules, model answers and fallbacks."""
import re
from sqlalchemy import select
from .db import Memory


def matching_owner_rules(db, product):
    scopes = {'global', 'product:' + product.id, 'brand:' + product.brand, 'category:' + product.category}
    scopes = {value.strip().casefold() for value in scopes}
    return [row.text for row in db.scalars(select(Memory).where(Memory.enabled == True))
            if row.scope.strip().casefold() in scopes]


def owner_business_facts(product, rules):
    if product.brand.strip().casefold() != 'eikosha':
        return []
    declaration = next((rule for rule in rules if re.search(r'официальн\w*\s+дистрибьютор', rule, re.I)
                        and re.search(r'eikosha|эйкоша|ейкоша', rule, re.I)
                        and not re.search(r'не\s+являем|не\s+официальн', rule, re.I)), '')
    if not declaration:
        return []
    return [{'kind': 'store_policy', 'text': 'Мы являемся официальным дистрибьютором EIKOSHA.',
             'source': 'Подтверждённое правило владельца магазина', 'source_type': 'owner_policy',
             'verification_status': 'VERIFIED', 'policy': 'eikosha_distributor'}]


def apply_owner_preferences(reply, review, rules):
    applied = []
    for rule in rules:
        lowered = rule.lower().replace('ё', 'е')
        if 'добрый день' in lowered and any(word in lowered for word in ('начин', 'приветств', 'отвеч', 'пиши')):
            reply = re.sub(r'^\s*(?:здравствуйте|добрый день)[!.,]?\s*', 'Добрый день! ', reply, flags=re.I)
            applied.append('приветствие')
        if review.text.strip() and ('уже опис' in lowered or 'уже указ' in lowered) and any(word in lowered for word in ('не спраш', 'не уточн', 'не прос')):
            reply = re.sub(r'[^.!?]*(?:уточните|расскажите)[^.!?]*(?:что именно[^.!?]*не устроило|какие свойства[^.!?]*не устроили|в ч[её]м проявилась проблема)[^.!?]*[.!?]?', '', reply, flags=re.I)
            applied.append('не переспрашивать описанную проблему')
        if any(word in lowered for word in ('не использ', 'не пиши')):
            for word in re.findall('[«“"]([^»”"]+)[»”"]', rule):
                reply = re.sub(re.escape(word), '', reply, flags=re.I)
                applied.append('запрет слова: ' + word)
    return re.sub(r'[ \t]{2,}', ' ', reply).strip(), list(dict.fromkeys(applied))
