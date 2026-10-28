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
    if not match:
        return ''
    # A question rule saying NOT to add the review signature is not itself a
    # review-only signature rule. Keep its greeting/format instructions active.
    before, after = rule[:match.start()], rule[match.end():]
    if (re.search(r'не\s+(?:добавля\w*|использ\w*|пиши)[^.!?\n]{0,60}$', before, re.I)
            or re.match(r'[^.!?\n]{0,90}\bне\s+(?:добавля\w*|использ\w*|пиши)', after, re.I)):
        return ''
    return match.group(1).strip()


def apply_review_signature(reply, rules):
    signature = next((value for rule in reversed(rules) if (value := review_signature(rule))), '')
    if not signature:
        return reply
    # Models may already include the closing line, including after regeneration.
    body = re.sub(r'(?:\s*' + re.escape(signature) + r')+\s*$', '', plain_text(reply), flags=re.I).rstrip()
    return body + '\n' + signature if body else signature


def matching_owner_rules(db, product, for_questions=False, *, rule_rows=None):
    from .shared_marketplaces import equivalent_products
    rule_rows = list(db.scalars(select(Memory).where(Memory.enabled == True).order_by(Memory.id))) if rule_rows is None else rule_rows
    if not rule_rows:
        return []
    counterparts = equivalent_products(db, product) if any(_scope_key(row.scope).startswith(('product:', 'category:')) for row in rule_rows) else [product]
    scopes = {'global', 'brand:' + product.brand, 'category:' + product.category}
    scopes.update('product:' + item.id for item in counterparts)
    scopes.update('category:' + item.category for item in counterparts if item.category)
    scopes = {_scope_key(value) for value in scopes}
    rows = [row for row in rule_rows
            if _scope_key(row.scope) in scopes
            and rule_applies_to(row.kind, for_questions)
            and not (for_questions and review_signature(row.text))]
    # General defaults precede narrower policies. Avoid database-dependent order.
    priority = {'global': 0, 'brand': 1, 'category': 1, 'product': 2}
    return [row.text for row in sorted(rows, key=lambda row: priority.get(_scope_key(row.scope).split(':')[0], 0))]


def _scope_key(value):
    kind, separator, target = (value or 'global').partition(':')
    return kind.strip().casefold() + (':' + target.strip().casefold() if separator else '')


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
    # All callers, including template/fallback answers, use the same parser.
    # Import lazily: the policy also uses matching_owner_rules for previews.
    from .owner_reply_policy import apply_local_preferences
    return apply_local_preferences(reply, review, rules, style_only=style_only)

