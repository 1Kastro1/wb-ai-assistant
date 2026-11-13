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
from types import SimpleNamespace
import re
from threading import RLock
from time import monotonic

VERSION = 'context-rules-2026-11-03-untried-review'
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


SIMPLE_PRAISE_TEXTS = frozenset({'', 'все супер', 'все отлично', 'все хорошо', 'отлично', 'супер',
    'спасибо', 'рекомендую', 'все понравилось', 'товар хороший', 'хороший товар', 'понравилось',
    'отличные лампочки', 'хорошая штука', 'отличный товар', 'отличное качество', 'хорошее качество',
    'оригинал спасибо', 'оригинал. спасибо', 'отригинал. спасибо', 'отригинал спасибо',
    'приятный запах', 'приятный аромат', 'аромат приятный', 'запах приятный',
    'запах пробивается как оригинал'})


def praise_text(review):
    text = (getattr(review, 'text', '') or '').casefold().replace('ё', 'е')
    # Known praise emoji and punctuation are decoration, never evidence that an
    # otherwise unknown or mixed message is positive. Match the WHOLE remainder.
    text = re.sub(r'[🔥👍❤♥️!.,…]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def simple_positive(review):
    """Exact whole-message praise only; never infer sentiment by substring."""
    text = praise_text(review)
    if not text and (getattr(review, 'text', '') or '').strip():
        return False
    # Compose only complete, known praise clauses. Unknown words (including
    # complaints/questions after a comma) keep the semantic verification path.
    clauses = [part.strip() for part in re.split(r'[,;.!\n]+',
        (getattr(review, 'text', '') or '').casefold().replace('ё','е')) if part.strip()]
    composed = 1 <= len(clauses) <= 4 and all(praise_text(SimpleNamespace(text=part))
        in SIMPLE_PRAISE_TEXTS for part in clauses)
    if text not in SIMPLE_PRAISE_TEXTS and not composed:
        return False
    # Reuse all existing safety/audience gates without mutating the input row.
    return empty_positive(SimpleNamespace(text='', **{key: getattr(review, key, default) for key, default in
        [('rating',0),('risk','NORMAL'),('manual',False),('safety_critical',False),('marketplace','')]}))


UNTRIED_TEXTS = frozenset({
    'позже дам информацию по качеству',
    'еще не пробовал', 'еще не пробовал позже дополню отзыв',
    'пока не использовал', 'на вид качественный еще не проверял',
})


def untried_template(review, rules, revision=0, *, product=None):
    """Exact neutral messages and separately audited whole policies only."""
    from types import SimpleNamespace
    from .owner_reply_policy import constraints, apply_local_preferences, apply_signature, local_issues
    from .product_guidance import positive_review_reply
    from .safety import validate_reply
    text = re.sub(r'[.!?,;…]', ' ', (getattr(review, 'text', '') or '').casefold().replace('ё', 'е'))
    if ' '.join(text.split()) not in UNTRIED_TEXTS:
        return None
    if not empty_positive(SimpleNamespace(text='', **{key: getattr(review, key, default) for key, default in
            [('rating',0),('risk','NORMAL'),('manual',False),('safety_critical',False),('marketplace','')]})):
        return None
    applicable = select_rules(rules, review, product)[0] if product is not None else rules
    if not rules or any(not profile(rule).get('local_untried') and constraints([rule])['semantic'] for rule in applicable):
        return None
    value = positive_review_reply(review, product, int(revision) % 3)
    if not value:
        return None
    value, _ = apply_local_preferences(value, review, rules)
    try:
        value = validate_reply(apply_signature(value, rules))
    except ValueError:
        return None
    return value if not local_issues(value, rules, review) else None


FAMILIES = {
    'batteries': r'аккумулятор|электролит|автомобильн\w*\s+батаре',
    'brakes': r'колодк|суппорт|тормозн\w*\s+(?:диск|механизм)',
    'filters': r'фильтр',
    'belts': r'ремень|ремн[яи]|натяжител|ролик|\bгрм\b',
    'suspension': r'рулев|подвеск|рычаг|сошк|наконечник|сайлентблок|амортизатор|шаров\w*\s+опор',
    'electrical': r'ламп|электрик|датчик|цокол|предохранител|проводк',
    'oils': r'масл[оа]\b|смазк|техническ\w*\s+жидк',
    'antifreeze': r'антифриз|охлаждающ\w*\s+жидк',
    'cleaners': r'очистител|чистящ|антижир|profoam|средств\w*\s+для\s+(?:мытья|уборки)',
    'body_care': r'полирол|воск|шампун|чернител|покрыти|кузов|окон|стекл|зеркал',
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
        families.add('wipers')
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
        elif families and (spec.get('family') or spec.get('families')) and not families.intersection(
                spec.get('families') or [spec['family']]):
            reason = 'Условное правило другой категории'
        if reason:
            skipped.append({'rule_hash': digest(rule), 'reason': reason})
        else:
            selected.append(rule)
    return selected, skipped


POSITIVE_BODIES = (
    'Спасибо за высокую оценку! Пусть покупка радует Вас каждый день!',
    'Спасибо, что оценили покупку! Желаем Вам приятных впечатлений от её использования!',
    'Спасибо за Вашу оценку! Пусть покупка будет Вам полезна!',
)

# Previously generated, reviewed no-claims answers remain valid; published
# replies and existing drafts must not be rewritten just to change their length.
PREVIOUS_RATING_BODIES = (
    'Спасибо за высокую оценку!',
    'Благодарим Вас за оценку покупки!',
    'Спасибо за Вашу оценку!',
)

SHORT_PRAISE_BODIES = (
    'Спасибо за добрые слова! Очень приятно, что покупка Вам понравилась. Пользуйтесь с удовольствием!',
    'Спасибо, что поделились впечатлениями! Рады, что Вы довольны покупкой. Пусть она и дальше радует Вас!',
    'Спасибо за отзыв! Здорово, что покупка оставила хорошее впечатление. Хорошего Вам дня!',
)

SCENT_PRAISE_BODIES = (
    'Спасибо за отзыв! Рады, что аромат Вам понравился. Пусть он и дальше радует Вас!',
    'Благодарим за отзыв! Приятно, что Вы отметили аромат. Желаем приятного использования!',
    'Спасибо, что поделились впечатлением! Рады, что запах Вам понравился. Желаем Вам хорошего дня!',
)

# Exact old built-in wording only. Used by the explicit, bounded refresh job;
# employee text and arbitrary previously approved prose are never rewritten.
LEGACY_POSITIVE_BODIES = (
    'Благодарим Вас за высокую оценку! Спасибо, что нашли время оценить покупку. Желаем Вам приятного использования и хорошего дня.',
    'Спасибо за высокую оценку! Нам важно Ваше мнение о покупке. Благодарим за уделённое время и желаем приятного использования.',
    'Благодарим за Вашу оценку! Спасибо за доверие к нашему магазину. Желаем Вам приятного использования покупки и хорошего дня.',
    'Спасибо за высокую оценку и добрые слова! Желаем Вам приятного использования покупки.',
    'Благодарим за положительный отзыв! Желаем Вам хорошего дня и приятного использования.',
)


def legacy_positive_template(answer, review, rules):
    from .owner_reply_policy import apply_local_preferences, apply_signature
    if not simple_positive(review):
        return False
    for body in LEGACY_POSITIVE_BODIES:
        value, _ = apply_local_preferences('Добрый день! ' + body, review, rules)
        if answer == apply_signature(value, rules):
            return True
    return False


def positive_body(review, revision=0):
    # Stable distribution across reviews; regeneration advances independently.
    identity = str(getattr(review, 'id', '') or getattr(review, 'wb_review_id', ''))
    seed = int(hashlib.sha256(identity.encode()).hexdigest()[:8], 16)
    bodies = SHORT_PRAISE_BODIES if (getattr(review, 'text', '') or '').strip() else POSITIVE_BODIES
    text = re.sub(r'\s+', ' ', (getattr(review, 'text', '') or '').casefold().replace('ё', 'е')).strip(' .!…\n\r\t')
    if text in {'приятный запах', 'приятный аромат', 'аромат приятный', 'запах приятный'}:
        bodies = SCENT_PRAISE_BODIES
    return bodies[(seed + max(0, int(revision))) % len(bodies)]


def positive_template(review, rules, revision=0, *, product=None):
    """A narrow, reviewed no-claims answer; arbitrary semantic rules opt out."""
    from .owner_reply_policy import constraints, apply_local_preferences, apply_signature, local_issues
    from .safety import validate_reply
    if not simple_positive(review):
        return untried_template(review, rules, revision, product=product)
    if not rules:
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


def verified_positive_reply(reply, review, rules, *, product=None):
    """Recheck exact reviewed no-claims wording; do not trust a prior timeout.

    This is a fresh verdict on current inputs/rules, not a timeout exemption.
    Unknown rules, complaints, questions and edited factual prose opt out.
    """
    from .owner_reply_policy import apply_local_preferences, apply_signature, local_issues
    from .safety import validate_reply
    if not simple_positive(review):
        return any(reply == untried_template(review, rules, i, product=product) for i in range(3))
    if not positive_template(review, rules, product=product):
        return False
    candidates = {positive_template(review, rules, i, product=product) for i in range(3)}
    if not (getattr(review, 'text', '') or '').strip():
        for body in PREVIOUS_RATING_BODIES:
            value, _ = apply_local_preferences('Добрый день! ' + body, review, rules)
            candidates.add(apply_signature(value, rules))
    if (getattr(review, 'text', '') or '').strip():
        body = 'Спасибо за Ваш отзыв. Рады, что покупка Вам понравилась. Желаем приятного использования!'
        value, _ = apply_local_preferences('Добрый день! ' + body, review, rules)
        candidates.add(apply_signature(value, rules))
    from .owner_reply_policy import context_constraints
    greetings = context_constraints(rules, review)['greetings']
    for candidate in list(candidates):
        for original in greetings:
            if candidate and candidate.startswith(original):
                candidates.update(greeting + candidate[len(original):] for greeting in greetings)
    try:
        return (reply in candidates or no_claims_positive_reply(reply, review, rules)) and validate_reply(reply) == reply and not local_issues(reply, rules, review)
    except ValueError:
        return False


# An audited grammar permits ordinary variations without trusting arbitrary
# model prose. Each complete sentence is reviewed; no product facts, promises,
# usage advice, names, numbers or quoted buyer instructions are admitted.
POSITIVE_THANKS = frozenset({
    'Спасибо за отзыв', 'Спасибо за Ваш отзыв', 'Спасибо за Вашу оценку',
    'Спасибо за высокую оценку', 'Спасибо за положительный отзыв',
    'Благодарим за отзыв', 'Благодарим Вас за отзыв',
    'Благодарим за положительный отзыв', 'Благодарим Вас за положительный отзыв',
    'Благодарим за высокую оценку', 'Благодарим Вас за высокую оценку',
})
POSITIVE_ACKNOWLEDGEMENTS = frozenset({
    'Рады, что покупка Вам понравилась', 'Рады, что Вы остались довольны покупкой',
    'Нам приятно, что Вы остались довольны покупкой',
    'Рады, что покупка оставила хорошее впечатление',
})
POSITIVE_WISHES = frozenset({
    'Желаем приятного использования', 'Желаем Вам приятного использования',
    'Желаем приятного использования покупки', 'Желаем Вам хорошего дня',
    'Желаем хорошего дня', 'Желаем удачных покупок',
    'Будем рады видеть Вас снова',
})


def no_claims_positive_reply(reply, review, rules):
    from .owner_reply_policy import context_constraints
    style = context_constraints(rules, review)
    value = reply.strip()
    signature = style.get('signature')
    if signature:
        if not value.endswith(signature):return False
        value = value[:-len(signature)].rstrip()
    greeting = next((g for g in (style['greetings'] or [style['greeting']]) if g and value.startswith(g)), None)
    if not greeting:return False
    body = value[len(greeting):].strip()
    sentences = [part.strip() for part in re.split(r'[.!]', body) if part.strip()]
    if not 1 <= len(sentences) <= 3 or sentences[0] not in POSITIVE_THANKS:return False
    rest = sentences[1:]
    if rest and rest[0] in POSITIVE_ACKNOWLEDGEMENTS:
        # A blank rating supplies no buyer experience to acknowledge.
        if not (getattr(review,'text','') or '').strip():return False
        rest = rest[1:]
    return not rest or len(rest)==1 and rest[0] in POSITIVE_WISHES


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


def audited_usage_replies(review, product, rules, facts):
    """Fresh proof of one exact recipe against reviewed whole policy texts.

    No approval of arbitrary prose or unknown rules. Instructions must still be
    present in the current product evidence; other complaints/steps opt out.
    """
    from .reply_policy import analyze_customer_message
    from .product_guidance import product_usage_reply, usage_excerpt
    from .owner_reply_policy import constraints, apply_local_preferences, apply_signature, local_issues
    from .vehicle_identifiers import vehicle_identifiers
    from .safety import validate_reply
    text = getattr(review, 'text', '') or ''
    name = (getattr(product,'name','') or '').casefold()
    if (not rules or getattr(product,'brand','').upper() != 'KANGAROO' or 'лосьон' not in name or 'кож' not in name
        or getattr(review,'risk','NORMAL') != 'NORMAL' or getattr(review,'manual',False)
        or getattr(review,'safety_critical',False) or getattr(review,'marketplace','').endswith('_question')
        or vehicle_identifiers(text)
        or re.search(r'ожог|аллерг|отрав|раздраж|травм|здоров|реб[её]н|питом|просроч|годност|дат[аыу]|наносил|прот[её]р|выдерж|по инструкц|смеш|уже|повред',text,re.I)):
        return set()
    analysis = analyze_customer_message(text, review.rating)
    if (set(analysis['complaints']) != {'NO_RESULT','SLIPPERY_SURFACE'} or analysis['is_question']
        or analysis['buyer_asks_about_money'] or analysis['resolved']):
        return set()
    instruction = usage_excerpt(facts)
    if not instruction or not re.search(r'нанес',instruction,re.I) or not re.search(r'протр|удал',instruction,re.I):
        return set()
    # Multiple incompatible dwell times need a full evidence review. Do not
    # certify an instruction simply because all source rows say VERIFIED.
    times = set(re.findall(r'\b\d+(?:[–-]\d+)?\s*(?:минут\w*|секунд\w*)', instruction, re.I))
    if len(times) > 1:
        return set()
    for rule in rules:
        if 'lotion_cleaning' not in profile(rule).get('local_cases', ()) and constraints([rule])['semantic']:
            return set()
    answers = set()
    thanks = ('Спасибо за Ваш отзыв.', 'Благодарим за обратную связь.', 'Спасибо, что поделились опытом.')
    for revision in range(3):
        original = product_usage_reply(review, product, facts, revision)
        for raw in (original, re.sub(r'(?:Спасибо|Благодарим)[^.!?]*[.!?]',lambda m:thanks[revision],original,count=1,flags=re.I)):
            answer, _ = apply_local_preferences(raw, review, rules)
            try:
                answer = validate_reply(apply_signature(answer, rules))
            except ValueError:
                continue
            if not local_issues(answer, rules, review):
                answers.add(answer)
    return answers


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



