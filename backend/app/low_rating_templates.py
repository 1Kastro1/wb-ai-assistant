"""Exact, contextual template allowlist for optional low-rating publication."""
import re
from .db import Product

SCENT_MESSAGES = {
    'аромат в машине так себе звучит', 'слабо пахнет', 'аромат слабый', 'запах слабый',
    'почти не пахнет', 'не пахнет', 'запах не понравился', 'аромат не понравился',
    'перестал пахнуть через неделю', 'быстро выветрился', 'аромат быстро выветрился',
    'пахнет один день',
}
SURFACE_MESSAGES = {'воск хороший но не стоит ждать чудо паутину видно', 'паутину видно', 'паутинка осталась'}


def template_key(review, product):
    if not review or not product or review.marketplace not in ('wb', 'ozon', 'yandex'):
        return None
    if not 1 <= review.rating <= 3 or review.manual or review.safety_critical or review.risk != 'NORMAL':
        return None
    from .reply_policy import analyze_customer_message
    from .review_cases import specific_review_case
    text = review.text.strip()
    if not text:
        return 'rating_only'
    analysis = analyze_customer_message(text, review.rating)
    # These require an individual answer, even if a scent/coating phrase occurs.
    if analysis['is_question'] or re.search(r'возврат|верну|деньг|компенсац|поддел|контраф|аллерг|ожог|отрав|голов|тошн|слом|поврежд|прот[её]к|достав|упаков|обман|брак', text, re.I):
        return None
    specific = specific_review_case(review, product)
    if specific:
        return None
    topics = set(analysis['complaints'])
    # A recognized complaint word does not prove there are no other problems.
    # Only reviewed complete buyer phrases qualify; added/unrecognized context
    # always stays with a person, even if the draft happens to match a template.
    normalized = text.lower().replace('ё','е')
    if re.search(r'[^а-яa-z0-9\s.,!?;:…«»"()—–-]', normalized):
        return None  # Do not erase meaningful emoji or unrecognized scripts.
    message = ' '.join(re.sub(r'[^а-яa-z0-9\s]', ' ', normalized).split())
    name = product.name.lower()
    if product.brand.upper() == 'EIKOSHA' and re.search(r'мелов|баноч|air spencer', name) and not re.search(r'giga|стерж', name):
        if message in SCENT_MESSAGES and topics and topics <= {'WEAK_SCENT', 'SCENT_IMPRESSION', 'ODOR_COMPLAINT'}:
            return 'eikosha_scent'
    from .product_guidance import fusso_instruction
    if message in SURFACE_MESSAGES and topics == {'SURFACE_MARKS'} and fusso_instruction(product):
        return 'fusso_surface'
    return None


def approved_template(db, review, draft):
    from .draft_audit import manually_edited
    from .services import trusted_product_facts, review_vent_holder
    from .safety import rule_reply, validate_reply
    from .owner_reply_rules import matching_owner_rules
    from .owner_reply_policy import apply_local_preferences, apply_signature
    from .product_guidance import fusso_instruction
    product = db.get(Product, review.product_id)
    key = template_key(review, product)
    if not key or manually_edited(draft):
        return False
    facts = trusted_product_facts(db, product)
    if key == 'eikosha_scent':
        holder = review_vent_holder(db, review, product)
        if holder: facts += [holder]
    if key == 'fusso_surface':
        facts += [fusso_instruction(product)]
    rules = matching_owner_rules(db, product)
    for revision in range(3):
        candidate = rule_reply(review, product, revision, facts=facts)
        if not candidate:
            continue
        candidate, _ = apply_local_preferences(candidate, review, rules)
        if validate_reply(apply_signature(candidate, rules)) == draft.text:
            return True
    return False
