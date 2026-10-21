"""Explicit owner preferences shared by rules, model answers and fallbacks."""
import re
from sqlalchemy import select
from .db import Memory
from .plain_text import plain_text


def review_signature(rule):
    """Read an explicit, quoted review signature from an owner rule."""
    if not re.search(r'отзыв', rule, re.I):
        return ''
    match = re.search(r'подпись\s*:?\s*[«“"]([^»”"\n]{1,200})[»”"]', rule, re.I)
    return match.group(1).strip() if match else ''


def apply_review_signature(reply, rules):
    signature = next((value for rule in reversed(rules) if (value := review_signature(rule))), '')
    if not signature:
        return reply
    # Models may already include the closing line, including after regeneration.
    body = re.sub(r'(?:\s*' + re.escape(signature) + r')+\s*$', '', plain_text(reply), flags=re.I).rstrip()
    return body + '\n' + signature if body else signature


def matching_owner_rules(db, product, for_questions=False):
    from .shared_marketplaces import equivalent_products
    counterparts = equivalent_products(db, product)
    scopes = {'global', 'brand:' + product.brand, 'category:' + product.category}
    scopes.update('product:' + item.id for item in counterparts)
    scopes.update('category:' + item.category for item in counterparts if item.category)
    scopes = {value.strip().casefold() for value in scopes}
    return [row.text for row in db.scalars(select(Memory).where(Memory.enabled == True))
            if row.scope.strip().casefold() in scopes
            and rule_applies_to(row.kind, for_questions)
            and not (for_questions and review_signature(row.text))]


def rule_applies_to(kind, for_questions=False):
    """Keep old common rules compatible; explicit kinds isolate both pipelines."""
    if kind == 'QUESTION_RULE':
        return for_questions
    if kind == 'REVIEW_RULE':
        return not for_questions
    return True


def proposed_rule_kind(text):
    return 'QUESTION_RULE' if re.search(r'для\s+(?:ответов\s+на\s+)?вопрос|ответ\w*\s+на\s+вопрос|при\s+вопрос', text, re.I) else 'REVIEW_RULE'


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


def apply_owner_preferences(reply, review, rules, style_only=False):
    applied = []
    for rule in rules:
        lowered = rule.lower().replace('ё', 'е')
        if 'добрый день' in lowered and any(word in lowered for word in ('начин', 'приветств', 'отвеч', 'пиши')):
            reply = re.sub(r'^\s*(?:здравствуйте|добрый день)[!.,]?\s*', 'Добрый день! ', reply, flags=re.I)
            applied.append('приветствие')
        if style_only:
            continue
        if review.text.strip() and ('уже опис' in lowered or 'уже указ' in lowered) and any(word in lowered for word in ('не спраш', 'не уточн', 'не прос')):
            reply = re.sub(r'[^.!?]*(?:уточните|расскажите)[^.!?]*(?:что именно[^.!?]*не устроило|какие свойства[^.!?]*не устроили|в ч[её]м проявилась проблема)[^.!?]*[.!?]?', '', reply, flags=re.I)
            applied.append('не переспрашивать описанную проблему')
        if any(word in lowered for word in ('не использ', 'не пиши')):
            for word in re.findall('[«“"]([^»”"]+)[»”"]', rule):
                reply = re.sub(re.escape(word), '', reply, flags=re.I)
                applied.append('запрет слова: ' + word)
    return re.sub(r'[ \t]{2,}', ' ', reply).strip(), list(dict.fromkeys(applied))

