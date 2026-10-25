"""Conservative rule planning and exact-input verification reuse.

Profiles refer to reviewed WHOLE rule texts by digest. Editing even one sentence
removes that optimisation until reviewed again. Unknown rules are always retained.
No model output can register a profile or certify its own fast path.
"""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from threading import RLock
from time import monotonic

VERSION = 'context-rules-2'
PROFILES = json.loads(Path(__file__).with_name('owner_rule_profiles.json').read_text(encoding='utf-8'))


def digest(text):
    return hashlib.sha256(' '.join(text.split()).encode()).hexdigest()


def profile(rule):
    return PROFILES.get(digest(rule), {})


def empty_positive(review):
    return (getattr(review, 'rating', 0) in (4, 5)
            and not (getattr(review, 'text', '') or '').strip()
            and getattr(review, 'risk', 'NORMAL') == 'NORMAL'
            and not getattr(review, 'manual', False)
            and not getattr(review, 'safety_critical', False)
            and not (getattr(review, 'marketplace', '') or '').endswith('_question'))


FAMILIES = {
    'parts': r'фильтр|колодк|ремень|свеч[аиу]|амортизатор|подшипник|рычаг|сошк|рулев|наконечник|сайлентблок|стартер|генератор|суппорт|сцеплен|тормозн\w*\s+диск',
    'chemicals': r'антифриз|тормозн\w*\s+жидк|моторн\w*\s+масл|трансмиссионн\w*\s+масл|присадк|смазк|очистител|обезжиривател',
    'cosmetics': r'полирол|шампун|чернител|защитн\w*\s+воск|очистител\w*\s+салон|автокосмет',
    'household': r'бытов\w*\s+хими|антижир|средств\w*\s+для\s+(?:мытья|стирки|уборки)|очистител',
    'fragrance': r'ароматизатор|освежител|автомобильн\w*\s+парфюм',
}


def category_evidence(review, product):
    value = ' '.join(str(getattr(obj, field, '') or '') for obj, fields in
                     ((product, ('name', 'category')), (review, ('text',))) for field in fields).lower().replace('ё', 'е')
    wipers = bool(re.search(r'стеклоочистител|дворник', value))
    # The stem «очиститель» inside «стеклоочиститель» is not auto chemistry.
    value = re.sub(r'стеклоочистител\w*', 'дворник', value)
    families = {name for name, pattern in FAMILIES.items() if re.search(pattern, value)}
    if wipers:
        families.add('parts')
    # A buyer can ask about another part in an unrelated product card.
    if re.search(r'\bvin\b|\bframe\b|\bвин\b|[a-z]{1,5}\d{1,4}[- ]\d{5,9}', value, re.I):
        families.add('parts')
    return families


def select_rules(rules, review, product):
    """Omit only certified conditional blocks with positive contrary evidence.

    Missing/unrecognised category data preserves all category rules. General
    restrictions and new or edited policies never disappear on keyword guesses.
    """
    selected, skipped = [], []
    families = category_evidence(review, product)
    for rule in rules:
        spec = profile(rule)
        reason = ''
        if spec.get('workflow'):
            reason = 'Правило рабочего процесса: подтверждение контролируется приложением'
        elif spec.get('empty_inapplicable') and empty_positive(review):
            reason = 'Высокая оценка без текста: условие жалобы отсутствует'
        elif families and spec.get('family') and spec['family'] not in families:
            reason = 'Условное правило другой категории'
        if reason:
            skipped.append({'rule_hash': digest(rule), 'reason': reason})
        else:
            selected.append(rule)
    return selected, skipped


POSITIVE_BODIES = (
    'Благодарим Вас за высокую оценку! Спасибо, что нашли время оценить покупку. Желаем Вам приятного использования и хорошего дня.',
    'Спасибо за высокую оценку! Нам важно Ваше мнение о покупке. Благодарим за уделённое время и желаем приятного использования.',
    'Благодарим за Вашу оценку! Спасибо за доверие к нашему магазину. Желаем Вам приятного использования покупки и хорошего дня.',
)


def positive_body(review, revision=0):
    # Stable distribution across reviews; regeneration advances independently.
    identity = str(getattr(review, 'id', '') or getattr(review, 'wb_review_id', ''))
    seed = int(hashlib.sha256(identity.encode()).hexdigest()[:8], 16)
    return POSITIVE_BODIES[(seed + max(0, int(revision))) % len(POSITIVE_BODIES)]


def positive_template(review, rules, revision=0, *, product=None):
    """A narrow, reviewed no-claims answer; arbitrary semantic rules opt out."""
    from .owner_reply_policy import constraints, apply_local_preferences, apply_signature, local_issues
    from .safety import validate_reply
    if not rules or not empty_positive(review):
        return None
    # Evaluate the same applicable policy as the final verifier. A certified
    # workflow/category exclusion must not disable the no-claims route, while an
    # unknown or edited directive must still reach semantic verification.
    applicable = select_rules(rules, review, product)[0] if product is not None else rules
    for rule in applicable:
        if not profile(rule).get('local_positive') and constraints([rule])['semantic']:
            return None
    body = positive_body(review, revision)
    value, _ = apply_local_preferences('Добрый день! ' + body, review, rules)
    try:
        value = validate_reply(apply_signature(value, rules))
    except ValueError:
        return None
    return value if not local_issues(value, rules) else None


def audited_case_template(review, product, rules):
    """Exact, no-new-facts response for a narrowly audited resolved complaint.

    Never approve arbitrary model text or new/edited semantic policy. Questions,
    other complaints, low ratings and safety/health cases remain model/manual.
    """
    from .owner_reply_policy import constraints, apply_local_preferences, apply_signature, local_issues
    from .reply_policy import analyze_customer_message
    from .review_cases import specific_review_case
    from .vehicle_identifiers import vehicle_identifiers
    from .safety import validate_reply
    text = getattr(review, 'text', '') or ''
    if (not rules or getattr(review, 'rating', 0) not in (4,5)
        or getattr(review, 'risk', 'NORMAL') != 'NORMAL'
        or getattr(review, 'manual', False) or getattr(review, 'safety_critical', False)
        or (getattr(review, 'marketplace', '') or '').endswith('_question')
        or vehicle_identifiers(text)
        or re.search(r'ожог|аллерг|отрав|раздраж|травм|врач|здоров|питом|реб[её]н|дет[ьи]|годност|просроч|дата|ден[ьь]?г|компенсац', text, re.I)):
        return None
    analysis = analyze_customer_message(text, review.rating)
    if (analysis['is_question'] or analysis['buyer_asks_about_money']
        or not analysis.get('sprayer_replaced')
        or not analysis['complaints']
        or set(analysis['complaints']) - {'DAMAGED_ITEM','QUALITY_ISSUE','PACKAGING_ISSUE','LEAKAGE'}):
        return None
    case = specific_review_case(review, product)
    if not case or case['key'] != 'replaced_sprayer':
        return None
    for rule in rules:
        if case['key'] not in profile(rule).get('local_cases', ()) and constraints([rule])['semantic']:
            return None
    answer, _ = apply_local_preferences(case['answer'], review, rules)
    try:
        answer = validate_reply(apply_signature(answer, rules))
    except ValueError:
        return None
    return answer if not local_issues(answer, rules, review) else None


_verdicts = OrderedDict()
_lock = RLock()
CACHE_LIMIT = 256
CACHE_SECONDS = 900


def clear_verdicts():
    with _lock:
        _verdicts.clear()


def verification_key(answer, review, product, rules, facts, instruction='', model_profile=None):
    def fields(obj, names):
        return {name: getattr(obj, name, None) for name in names}
    data = {'version': VERSION, 'answer': answer, 'rules': list(rules), 'facts': facts,
            'instruction': instruction, 'model_profile': model_profile,
            'review': fields(review, ('id', 'wb_account_id', 'wb_review_id', 'product_id', 'rating', 'text',
                                     'created_at', 'risk', 'manual', 'safety_critical', 'topics', 'marketplace')),
            'product': fields(product, ('id', 'name', 'brand', 'category', 'part_number', 'facts', 'marketplace'))}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def cached_verdict(key):
    with _lock:
        item = _verdicts.get(key)
        if not item:
            return None
        if item[0] <= monotonic():
            del _verdicts[key]
            return None
        _verdicts.move_to_end(key)
        return deepcopy(item[1])


def remember_verdict(key, answer, result):
    # Partial checks, failures and timeouts are never reusable approvals.
    if result.get('checked') is not True or result.get('passed') is not True or result.get('issues'):
        return
    with _lock:
        _verdicts[key] = (monotonic() + CACHE_SECONDS, deepcopy((answer, result)))
        _verdicts.move_to_end(key)
        while len(_verdicts) > CACHE_LIMIT:
            _verdicts.popitem(last=False)
