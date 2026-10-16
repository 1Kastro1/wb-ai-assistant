from .fitment_catalog import VIN, vehicle_identifiers, fitment_answer, oem_numbers
from .product_guidance import usage_excerpt, product_usage_reply, battery_voltage_answer
from .verified_instructions import prefer_manufacturer_instructions
from .assortment_questions import assortment_question_answer
import difflib
import asyncio
import time
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, case, or_, not_, text as sql_text
from .db import *
from .safety import rule_reply, review_intent, safe_fallback_reply, validate_reply, mask_vin, cleaner_effectiveness_complaint, fresh_reply_variants
from .integrations import ollama, wb, web_research, provider_for
from .generation_budget import GenerationBudget
from .reply_policy import analyze_customer_message, missing_complaints, customer_reply_policy, official_support_reply
from .db import IS_SQLITE
from .config import MODEL, REAL_PUBLISH


WORDS = re.compile(r'[а-яё0-9]{4,}', re.I)
MIN_DRAFT_QUALITY = 96
MAX_GENERATION_ATTEMPTS = 5
MIN_AI_CANDIDATES = 3
QUALITY_EVALUATOR_VERSION = '3.0'
WEB_FACT_MAX_AGE_DAYS = 30


def trusted_product_facts(db, product):
    """Only product-card facts and owner-approved manufacturer-domain facts reach the model."""
    official = str(setting(db, f'official_domain:{product.id}', '') or '').lower()
    result = []
    for fact in product.facts or []:
        if fact.get('verification_status') != 'VERIFIED':
            continue
        source = str(fact.get('source', ''))
        if source == 'Карточка товара' or source.startswith('Карточка WB:'):
            result.append(fact)
            continue
        parsed = urlsplit(source)
        host = (parsed.hostname or '').lower()
        if parsed.scheme == 'https' and official and (host == official or host.endswith('.' + official)):
            result.append(fact)
    from .steam_deo_guidance import reviewed_steam_facts
    result.extend(fact for fact in reviewed_steam_facts(product) if fact not in result)
    return prefer_manufacturer_instructions(result)


def draft_public(db, draft, actors=None):
    if not draft:
        return None
    item = public(draft)
    for key in ('created_by', 'updated_by'):
        actor = (actors.get(item[key]) if actors is not None else db.get(User, item[key])) if item[key] else None
        item[key + '_name'] = actor.display_name if actor else ('Автоматизация' if not item[key] else 'Бывший сотрудник')
    item['edits'] = [dict(edit) for edit in item.get('edits') or []]
    for edit in item['edits']:
        actor_id = edit.get('by')
        actor = (actors.get(actor_id) if actors is not None else db.get(User, actor_id)) if actor_id and actor_id != 'automation' else None
        edit['by_name'] = actor.display_name if actor else ('Автоматизация' if actor_id in ('', None, 'automation') else 'Бывший сотрудник')
    return item
INTENT_LABELS = {
    'WRONG_ITEM': 'пришёл другой товар', 'MISSING_PARTS': 'неполная комплектация',
    'DAMAGED_ITEM': 'повреждение товара', 'FITMENT_PROBLEM': 'товар не подошёл',
    'NO_RESULT': 'нет ожидаемого результата', 'ODOR_COMPLAINT': 'неприятный запах',
    'WEAK_SCENT': 'слабый аромат', 'DESCRIPTION_MISMATCH': 'несоответствие описанию',
    'PACKAGING_ISSUE': 'проблема с упаковкой', 'DELIVERY_ISSUE': 'проблема с доставкой',
    'USAGE_QUESTION': 'вопрос по применению', 'POSITIVE_EXPERIENCE': 'положительный опыт',
    'GENERAL': 'общее впечатление',
}


def _addresses_review_topic(review_text, reply_text):
    """Recognise semantic topic matches that do not share the same word ending."""
    review_value = review_text.lower().replace('ё', 'е')
    reply_value = reply_text.lower().replace('ё', 'е')
    topic_groups = (
        (('очистител', 'чистит', 'отмыл', 'пятн', 'гудрон', 'смол'), ('очист', 'загряз', 'пятн', 'гудрон', 'смол')),
        (('не оправдал', 'разочаров'), ('ожидан', 'разочаров', 'извин')),
        (('свет', 'тускл', 'слабо свет'), ('свет', 'ламп', 'ярк', 'напряж')),
        (('производител', 'маркиров', 'происхожд'), ('маркиров', 'расхожд', 'соответств')),
        (('деньги на ветер', 'толку нет', 'нет результата', 'не увидела результата', 'не увидел результата', 'бесполезн', 'не очищает', 'не помог'), ('результат', 'очистк', 'инструкц', 'применен', 'эффект')),
        (('отмывает', 'нагар', 'жир'), ('очистк', 'загряз', 'нагар', 'жир')),
        (('качеств', 'на совесть', 'клапан', 'резьба'), ('качеств', 'клапан', 'резьба')),
        (('подош', 'встали как родные'), ('установ', 'подош', 'подбор')),
        (('отработал', 'работает', 'рабочими'), ('работ', 'результат')),
        (('третий раз', 'пользуюсь давно', 'не первый раз'), ('снова', 'повторн', 'доверие')),
        (('вовремя', 'во время'), ('вовремя', 'достав')),
        (('пенится',), ('пенообраз', 'пенится')),
        (('соответствует описанию',), ('соответств', 'описан')),
        (('понравил',), ('понравил', 'порад')),
        (('протек', 'потек', 'крышка'), ('протеч', 'упаков', 'крышк')),
        (('развод', 'вазелин'), ('развод', 'жирный слой')),
        (('воня', 'запах', 'аромат', 'пах'), ('воня', 'запах', 'аромат', 'пах')),
        (('скрип', 'свист', 'шум'), ('скрип', 'свист', 'шум')),
        (('тормоз', 'педал', 'скрежет'), ('тормоз', 'педал', 'скрежет', 'диагност')),
        (('сломан', 'разбит', 'поврежд', 'помят'), ('сломан', 'разбит', 'поврежд', 'помят')),
        (('не подош', 'не подходит', 'несовмест'), ('не подош', 'не подходит', 'совместим')),
    )
    return any(
        any(marker in review_value for marker in review_markers)
        and any(marker in reply_value for marker in reply_markers)
        for review_markers, reply_markers in topic_groups
    )


def owner_style_profile(db, account_id='owner'):
    answered = db.scalar(select(func.count()).select_from(Review).where(Review.wb_account_id == account_id, Review.is_answered == True, Review.existing_answer != '')) or 0
    cache_key = 'learned_reply_style:' + account_id
    cached = setting(db, cache_key)
    if cached and cached.get('answered_count') == answered:
        return cached
    texts = list(db.scalars(select(Review.existing_answer).where(Review.wb_account_id == account_id, Review.is_answered == True, Review.existing_answer != '')))
    greetings = Counter('Добрый день' if t.lower().startswith('добрый день') else 'Здравствуйте' if t.lower().startswith('здравствуйте') else 'другое' for t in texts)
    profile = {
        'answered_count': answered,
        'preferred_greeting': greetings.most_common(1)[0][0] if texts else 'Здравствуйте',
        'average_length': round(sum(map(len, texts)) / len(texts)) if texts else 0,
        'uses_team_signature': sum('команда юником' in t.lower() for t in texts) > len(texts) / 3 if texts else False,
        'return_guidance_examples': sum('возврат' in t.lower() for t in texts),
        'owner_preferences': ['3–5 предложений', 'тёплый и дружелюбный тон', 'ответ по существу отзыва', 'эмодзи только для позитивных отзывов'],
    }
    set_setting(db, cache_key, profile)
    db.commit()
    return profile


def previous_answer_examples(db, review, product, limit=5):
    candidates = list(db.scalars(
        select(Review).join(Product).where(
            Review.is_answered == True,
            Review.existing_answer != '',
            Review.wb_account_id == review.wb_account_id,
            (Review.product_id == product.id) | (Product.brand == product.brand) | (Product.category == product.category),
        ).order_by(Review.created_at.desc()).limit(400)
    ))
    wanted = set(WORDS.findall(review.text.lower()))
    scored = []
    for old in candidates:
        old_product = db.get(Product, old.product_id)
        overlap = len(wanted & set(WORDS.findall((old.text + ' ' + old_product.name).lower())))
        score = overlap * 4 + (20 if old.product_id == product.id else 0) + (3 if old_product.brand == product.brand else 0) + (1 if old_product.category == product.category else 0)
        scored.append((score, old.created_at, {'review': old.text[:1000], 'answer': old.existing_answer[:2000], 'product': old_product.name, 'same_product': old.product_id == product.id}))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[:limit] if item[0] > 0]


def has_usage_instruction(facts):
    text = ' '.join(str(f.get('text', '')) for f in facts).lower()
    return any(word in text for word in ('способ применен', 'применение', 'нанесите', 'нанести', 'распылите', 'встряхните', 'используйте', 'рекомендуется обработать', 'обработать поверхность'))


def needs_usage_instruction(review, product=None):
    if review_intent(review.text) == 'NO_RESULT':
        return True
    if product is not None and cleaner_effectiveness_complaint(review, product):
        return True
    text = review.text.lower().replace('ё', 'е')
    return any(phrase in text for phrase in ('как пользоваться', 'как использовать', 'как применять', 'как нанести', 'как наносить', 'способ применения', 'нужна инструкция'))


def positive_rating_reply(product, revision=1):
    """Useful rotating replies for ratings that do not contain review text."""
    variants = [
        f'Здравствуйте! Большое спасибо за высокую оценку! 😊 Нам очень приятно, что вы выбрали товар «{product.name}». Надеемся, покупка будет радовать вас при использовании. Будем рады видеть вас снова!',
        f'Здравствуйте! Благодарим вас за хорошую оценку товара «{product.name}»! 😊 Для нас очень ценно ваше доверие. Желаем, чтобы покупка приносила только положительные впечатления. Возвращайтесь к нам снова!',
        f'Здравствуйте! Спасибо, что оценили товар «{product.name}»! 😊 Рады, что покупка заслужила вашу высокую оценку. Пользуйтесь с удовольствием, а мы будем стараться и дальше радовать вас качественными товарами!',
    ]
    return variants[max(0, int(revision)) % len(variants)]


def cached_web_instruction_sources(product):
    cutoff = datetime.now(timezone.utc) - timedelta(days=WEB_FACT_MAX_AGE_DAYS)
    results = []
    for fact in product.facts or []:
        if fact.get('verification_status') != 'WEB_UNVERIFIED' or not fact.get('source'):
            continue
        try:
            researched_at = datetime.fromisoformat(fact.get('researched_at', ''))
            if researched_at.tzinfo is None:
                researched_at = researched_at.replace(tzinfo=timezone.utc)
            if researched_at < cutoff:
                continue
        except (TypeError, ValueError):
            continue
        results.append({'title': fact.get('title', ''), 'snippet': fact.get('text', ''), 'url': fact.get('source', ''), 'host': fact.get('host', ''), 'match_score': fact.get('match_score', 0)})
    return results[:5]


def remember_web_instruction_sources(product, sources):
    preserved = [f for f in (product.facts or []) if f.get('verification_status') != 'WEB_UNVERIFIED']
    product.facts = preserved + [{
        'text': source.get('snippet', ''),
        'title': source.get('title', ''),
        'source': source.get('url', ''),
        'host': source.get('host', ''),
        'match_score': source.get('match_score', 0),
        'researched_at': now(),
        'verification_status': 'WEB_UNVERIFIED',
    } for source in sources]


def _review_query(filters):
    view = filters.get('view', 'all')
    query = select(Review).join(Product)
    if view in ('manual', 'wrong_item', 'no_effect', 'odor'):
        query = query.outerjoin(Draft, Draft.review_id == Review.id)
    conditions = [Review.rating <= filters.get('max_rating', 5), ~Review.marketplace.like('%_question')]
    if filters.get('q'):
        q = filters['q']
        conditions.append(Review.text.contains(q, autoescape=True) | Product.name.contains(q, autoescape=True) | Product.brand.contains(q, autoescape=True))
    if filters.get('unanswered'):
        conditions.append(Review.is_answered == False)
    if filters.get('attention'):
        conditions.append((Review.rating <= 3) | (Review.manual == True) | (Review.risk != 'NORMAL') | (Review.status == 'manual_review'))
    if filters.get('product_id'):
        conditions.append(Review.product_id == filters['product_id'])
    if filters.get('account_id'):
        conditions.append(Review.wb_account_id == filters['account_id'])
    if filters.get('days'):
        days = filters['days']
        start = datetime.now(timezone(timedelta(hours=3))).replace(hour=0, minute=0, second=0, microsecond=0) if days == 1 else datetime.now(timezone.utc) - timedelta(days=days)
        conditions.append(Review.created_at >= start.isoformat())
    if view == 'positive':
        conditions.append(Review.rating.between(4, 5))
    elif view == 'negative':
        conditions.append(Review.rating.between(1, 3))
    elif view == 'rating_only':
        text_without_whitespace = func.replace(func.replace(func.replace(func.coalesce(Review.text, ''), '\n', ''), '\r', ''), '\t', '')
        conditions.append(func.trim(text_without_whitespace) == '')
    elif view == 'context':
        raw_ids = filters.get('review_ids', '')
        if len(raw_ids) > 12000:
            raise ValueError('Слишком длинная выборка отзывов')
        ids = list(dict.fromkeys(part.strip() for part in raw_ids.split(',') if part.strip()))
        if len(ids) > 200:
            raise ValueError('Выберите не более 200 отзывов')
        conditions.append(Review.id.in_(ids))
    elif view == 'manual':
        conditions.append(or_(Review.manual == True, Review.status == 'manual_review',
                              Draft.quality['passed'].as_boolean() == False,
                              Draft.model.startswith('safety-fallback')))
    elif view in ('wrong_item', 'no_effect', 'odor'):
        # Match the visible topic labels and review text before pagination.
        topic_text = func.coalesce(Draft.quality['intent_label'].as_string(), '') + ' ' + Review.text
        folded = func.unicode_casefold(topic_text) if IS_SQLITE else func.lower(topic_text)
        def contains_any(phrases):
            return or_(*(folded.like('%' + phrase + '%') for phrase in phrases))
        wrong_item = contains_any(('не тот', 'другой товар', 'перепутал', 'wrong'))
        negative_effect_phrase = (func.unicode_no_effect(topic_text) == 1 if IS_SQLITE else
                                  topic_text.op('~*')(r'(^|[^[:alpha:]])не[[:space:]]+(помог|работает|отмыл|очищает|очистил)'))
        no_effect = (contains_any(('нет результата', 'бесполезн', 'неэффектив')) |
                     negative_effect_phrase |
                     ((Review.rating <= 3) & contains_any(('эффект', 'результат'))))
        odor_topic = contains_any(('запах', 'воняет', 'аромат', 'пахнет'))
        dislike = (func.unicode_dislike(topic_text) == 1 if IS_SQLITE else
                   topic_text.op('~*')(r'(^|[^[:alpha:]])не[[:space:]]+понрав'))
        odor_complaint = contains_any(('неприят', 'воняет', 'резк', 'ужасн', 'отвратительн')) | dislike
        odor = odor_topic & ((Review.rating <= 3) | odor_complaint | (Draft.quality['intent'].as_string() == 'ODOR_COMPLAINT'))
        conditions.append(wrong_item if view == 'wrong_item' else
                          no_effect & not_(wrong_item) if view == 'no_effect' else
                          odor & not_(wrong_item) & not_(no_effect))
    return query.where(*conditions)


def review_items(db, rows):
    """Serialize one page with bounded queries, including authors of historical edits."""
    if not rows:
        return []
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_({r.product_id for r in rows})))}
    drafts = {d.review_id: d for d in db.scalars(select(Draft).where(Draft.review_id.in_([r.id for r in rows])))}
    actor_ids = {actor for d in drafts.values() for actor in (d.created_by, d.updated_by) if actor}
    actor_ids.update(edit.get('by') for d in drafts.values() for edit in d.edits or [] if edit.get('by') not in (None, '', 'automation'))
    actors = {u.id: u for u in db.scalars(select(User).where(User.id.in_(actor_ids)))} if actor_ids else {}
    result = []
    for row in rows:
        item = public(row)
        item['product'] = public(products.get(row.product_id))
        item['draft'] = draft_public(db, drafts.get(row.id), actors)
        result.append(item)
    return result


def reviews_search(db, q='', unanswered=False, max_rating=5, days=0, product_id='', limit=50, offset=0, account_id='', attention=False, view='all', review_ids=''):
    query = _review_query(locals())
    rows = db.scalars(query.order_by(Review.created_at.desc(), Review.id).offset(max(0, offset)).limit(min(200, max(1, limit)))).all()
    return review_items(db, rows)


def reviews_page(db, **filters):
    limit = min(200, max(1, int(filters.get('limit', 50))))
    offset = max(0, int(filters.get('offset', 0)))
    rows = reviews_search(db, **{**filters, 'limit': limit, 'offset': offset})
    total = db.scalar(_review_query(filters).with_only_columns(func.count()).order_by(None)) or 0
    return {'items': rows, 'total': total, 'limit': limit, 'offset': offset, 'has_more': offset + len(rows) < total}


def review_filter_counts(db, q='', account_id=''):
    query = _review_query({'q':q, 'account_id':account_id}).outerjoin(Draft, Draft.review_id == Review.id)
    empty_text = func.trim(func.replace(func.replace(func.replace(func.coalesce(Review.text, ''), '\n', ''), '\r', ''), '\t', '')) == ''
    manual = or_(Review.manual == True, Review.status == 'manual_review',
                 Draft.quality['passed'].as_boolean() == False, Draft.model.startswith('safety-fallback'))
    values = db.execute(query.with_only_columns(func.count(),
        func.sum(case((Review.rating.between(4, 5), 1), else_=0)),
        func.sum(case((Review.rating.between(1, 3), 1), else_=0)),
        func.sum(case((empty_text, 1), else_=0)),
        func.sum(case((manual, 1), else_=0))).order_by(None)).one()
    return dict(zip(('all', 'positive', 'negative', 'rating_only', 'manual'), (value or 0 for value in values)))


def assess_reply(review, product, reply, verified_facts=None):
    """Deterministic quality gate; it explains weaknesses without trusting the model to grade itself."""
    lowered = reply.lower().replace('ё', 'е')
    review_words = set(WORDS.findall(review.text.lower()))
    reply_words = set(WORDS.findall(lowered))
    issues, score = [], 40
    breakdown = {
        'safety': {'label': 'Безопасность', 'score': 40, 'max': 40},
        'substance': {'label': 'Содержательность', 'score': 0, 'max': 15},
        'courtesy': {'label': 'Дружелюбность', 'score': 0, 'max': 10},
        'empathy': {'label': 'Эмпатия', 'score': 0, 'max': 10},
        'relevance': {'label': 'Соответствие отзыву', 'score': 0, 'max': 12},
        'resolution': {'label': 'Полезность решения', 'score': 0, 'max': 18},
    }
    if 120 <= len(reply) <= 900:
        score += 15
        breakdown['substance']['score'] = 15
    else:
        issues.append('ответ должен быть содержательным, без лишней длины')
    if any(x in lowered for x in ('спасибо', 'благодар')):
        score += 10
        breakdown['courtesy']['score'] = 10
    else:
        issues.append('добавить благодарность')
    if review.rating <= 3 and any(x in lowered for x in ('жаль', 'извин', 'сожале')):
        score += 10
        breakdown['empathy']['score'] = 10
    elif review.rating <= 3:
        issues.append('признать проблему и проявить эмпатию')
    else:
        score += 10
        breakdown['empathy']['score'] = 10
    overlap = review_words & reply_words
    if overlap or len(review_words) <= 1 or _addresses_review_topic(review.text, reply):
        score += 12
        breakdown['relevance']['score'] = 12
    else:
        issues.append('ответить именно на содержание отзыва')
    analysis = analyze_customer_message(review.text, review.rating)
    for topic in missing_complaints(analysis, reply):
        issues.append('не учтено существенное замечание: ' + INTENT_LABELS.get(topic, topic))
    intent = review_intent(review.text)
    combined_cleaner_complaint = cleaner_effectiveness_complaint(review, product)
    if intent == 'WRONG_ITEM':
        if any(x in lowered for x in ('извин', 'сожале', 'жаль')):
            score += 18
            breakdown['resolution']['score'] = 18
        else:
            issues.append('извиниться за получение неверного товара')
    elif intent == 'NO_RESULT' or combined_cleaner_complaint:
        if any(x in lowered for x in ('инструкц', 'нанес', 'обработ', 'примен', 'распыл', 'протр')):
            score += 18
            breakdown['resolution']['score'] = 18
        else:
            issues.append('дать безопасную инструкцию по применению')
        verified_facts_text = ' '.join(
            str(fact.get('text', '')) for fact in (verified_facts if verified_facts is not None else (getattr(product, 'facts', None) or []))
            if fact.get('verification_status') == 'VERIFIED'
        ).lower().replace('ё', 'е')
        verified_facts_text = usage_excerpt(verified_facts if verified_facts is not None else (getattr(product, 'facts', None) or [])).lower().replace('ё', 'е')
        missing_steps = []
        if 'распыл' in verified_facts_text and 'распыл' not in lowered:
            missing_steps.append('распыление')
        if ('30 секунд' in verified_facts_text or '30 сек' in verified_facts_text) and not ('30 секунд' in lowered or '30 сек' in lowered):
            missing_steps.append('выдержка 30 секунд')
        if ('ткан' in verified_facts_text or 'салфет' in verified_facts_text) and not ('ткан' in lowered or 'салфет' in lowered):
            missing_steps.append('удаление чистой тканью или салфеткой')
        if combined_cleaner_complaint and missing_steps:
            issues.append('указать подтверждённые шаги применения: ' + ', '.join(missing_steps))
        if combined_cleaner_complaint and any(x in review.text.lower() for x in ('воня', 'запах', 'аромат', 'пах')) and not any(x in lowered for x in ('воня', 'запах', 'аромат', 'пах')):
            issues.append('признать замечание о неприятном запахе')
    else:
        score += 10
        breakdown['resolution']['score'] = 10
    if reply.strip().lower() in ('спасибо за отзыв!', 'спасибо за отзыв.', 'спасибо!'):
        score = min(score, 35)
        issues.append('слишком общий ответ')
    if review.rating >= 4 and not analysis['complaints'] and intent in ('POSITIVE_EXPERIENCE', 'GENERAL') and (intent == 'POSITIVE_EXPERIENCE' or any(x in review.text.lower().replace('ё','е') for x in ('соответствует описанию', 'все подошло', 'все понравилось', 'работает отлично', 'хорошее качество'))):
        if any(x in lowered for x in ('жаль', 'сожале', 'извин', 'не оправдал', 'разочаровал', 'показался слаб', 'недостаточно выражен')) or re.search(r'(?<!\w)не\s+понравил', lowered):
            score = min(score, 70)
            issues.append('не трактовать положительный отзыв как жалобу')
            breakdown['relevance']['score'] = 0
        elif any(x in lowered for x in ('ближе к потоку воздуха', 'аромат будет ощущаться лучше')):
            score = min(score, 80)
            issues.append('не предлагать решение проблемы, которой покупатель не описал')
    if review.rating >= 4 and analysis['sentiment'] != 'mixed' and intent in ('WRONG_ITEM','MISSING_PARTS','DAMAGED_ITEM','FITMENT_PROBLEM','NO_RESULT','ODOR_COMPLAINT','WEAK_SCENT','DESCRIPTION_MISMATCH','PACKAGING_ISSUE','DELIVERY_ISSUE'):
        score = min(score, 80)
        issues.append('оценка и текст отзыва противоречат друг другу — проверьте тон вручную')
    if review.rating <= 2 and intent == 'POSITIVE_EXPERIENCE':
        score = min(score, 80)
        issues.append('оценка и текст отзыва противоречат друг другу — проверьте тон вручную')
    if any(x in lowered for x in ('распылите', 'распылить', 'выдержите', 'оставьте на 30', 'ближе к потоку воздуха')):
        facts_text = ' '.join(str(f.get('text', '')) for f in (verified_facts if verified_facts is not None else (getattr(product, 'facts', None) or []))).lower().replace('ё','е')
        if not facts_text or not any(x in facts_text for x in ('распыл', 'выдерж', '30 секунд', 'потоку воздуха')):
            score = min(score, 75)
            issues.append('способ применения не подтверждён сведениями о товаре')
    return {
        'score': min(100, score), 'passed': score >= MIN_DRAFT_QUALITY and not issues,
        'issues': list(dict.fromkeys(issues)), 'breakdown': breakdown,
        'intent': intent, 'intent_label': 'недовольство результатом и неприятный запах' if combined_cleaner_complaint and any(x in review.text.lower() for x in ('воня', 'запах', 'аромат', 'пах')) else INTENT_LABELS.get(intent, INTENT_LABELS['GENERAL']),
        'checked_at': now(), 'evaluator_version': QUALITY_EVALUATOR_VERSION,
    }


def assess_candidate(review, product, reply, comparisons=(), verified_facts=None):
    """Apply the normal quality gate and penalise near-copies of earlier answers."""
    quality = assess_reply(review, product, reply, verified_facts)
    similarities = [difflib.SequenceMatcher(None, reply.lower(), text.lower()).ratio() for text in comparisons if text]
    similarity = max(similarities, default=0.0)
    quality['breakdown']['originality'] = {
        'label': 'Оригинальность', 'score': round((1 - similarity) * 100), 'max': 100,
    }
    quality['similarity'] = round(similarity, 3)
    if similarity >= 0.86:
        quality['score'] = max(0, quality['score'] - 20)
        quality['issues'] = list(dict.fromkeys([*quality['issues'], 'не повторять предыдущие ответы']))
        quality['passed'] = False
    return quality


def learn_from_edit(db, draft, old, new):
    if old.strip() == new.strip():
        return
    review = db.get(Review, draft.review_id)
    key = 'learned_edit_preferences:' + review.wb_account_id
    learned = setting(db, key, {'edit_count': 0, 'longer': 0, 'shorter': 0, 'greeting_added': 0, 'apology_added': 0})
    learned['edit_count'] = int(learned.get('edit_count', 0)) + 1
    learned['longer' if len(new) > len(old) else 'shorter'] = int(learned.get('longer' if len(new) > len(old) else 'shorter', 0)) + 1
    if not old.lower().startswith(('здравствуйте', 'добрый день')) and new.lower().startswith(('здравствуйте', 'добрый день')):
        learned['greeting_added'] = int(learned.get('greeting_added', 0)) + 1
    if not any(x in old.lower() for x in ('жаль', 'извин', 'сожале')) and any(x in new.lower() for x in ('жаль', 'извин', 'сожале')):
        learned['apology_added'] = int(learned.get('apology_added', 0)) + 1
    set_setting(db, key, learned)


def quality_report(db, days=30, account_id=''):
    filters = [~Review.marketplace.like('%_question')]
    if days:
        filters.append(Review.created_at >= (datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
    if account_id:
        filters.append(Review.wb_account_id == account_id)
    drafts = list(db.scalars(select(Draft).join(Review).where(*filters)))
    changed = False
    for draft in drafts:
        if not draft.quality or draft.quality.get('evaluator_version') != QUALITY_EVALUATOR_VERSION:
            review = db.get(Review, draft.review_id)
            product = db.get(Product, review.product_id)
            draft.quality = assess_reply(review, product, draft.text, trusted_product_facts(db, product))
            changed = True
    if changed:
        db.commit()
    scores = [int((d.quality or {}).get('score', 0)) for d in drafts if (d.quality or {}).get('score') is not None]
    edited = [d for d in drafts if d.edits]
    return {
        'period_days': days, 'drafts': len(drafts), 'passed': sum(bool((d.quality or {}).get('passed')) for d in drafts),
        'average_score': round(sum(scores) / len(scores), 1) if scores else 0,
        'edited': len(edited), 'edit_rate': round(len(edited) / len(drafts) * 100, 1) if drafts else 0,
        'fallbacks': sum('fallback' in d.model for d in drafts),
        'learned_preferences': setting(db, 'learned_edit_preferences:' + (account_id or 'owner'), {}),
    }


def analytics(db, days=0, product_id='', account_id=''):
    filters = [~Review.marketplace.like('%_question')]
    if days:
        filters.append(Review.created_at >= (datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
    if product_id:
        filters.append(Review.product_id == product_id)
    if account_id:
        filters.append(Review.wb_account_id == account_id)
    aggregate = select(
        func.count(),
        func.sum(case((Review.is_answered == False, 1), else_=0)),
        func.sum(case((Review.rating <= 3, 1), else_=0)),
        func.sum(case(((Review.manual == True) & (Review.is_answered == False), 1), else_=0)),
        func.sum(case((Review.is_answered == True, 1), else_=0)),
        func.avg(Review.rating),
    ).select_from(Review).where(*filters)
    total, unanswered, negative, manual, published, average = db.execute(aggregate).one()
    topic_where = ["r.marketplace NOT LIKE '%_question'"]
    topic_params = {}
    if days:
        topic_where.append('r.created_at >= :topic_start')
        topic_params['topic_start'] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if product_id:
        topic_where.append('r.product_id = :topic_product_id')
        topic_params['topic_product_id'] = product_id
    if account_id:
        topic_where.append('r.wb_account_id = :topic_account_id')
        topic_params['topic_account_id'] = account_id
    topic_function = 'json_array_elements_text' if db.bind.dialect.name == 'postgresql' else 'json_each'
    topic_sql = f'SELECT j.value, COUNT(*) FROM reviews AS r, {topic_function}(r.topics) AS j'
    if topic_where:
        topic_sql += ' WHERE ' + ' AND '.join(topic_where)
    topic_sql += ' GROUP BY j.value ORDER BY j.value'
    topics = dict(db.execute(sql_text(topic_sql), topic_params).all())
    problem_rows = db.execute(
        select(Product.id, Product.name, Product.brand, func.count(Review.id), func.avg(Review.rating))
        .join(Review, Review.product_id == Product.id)
        .where(*filters, Review.rating <= 3)
        .group_by(Product.id, Product.name, Product.brand)
        .order_by(func.count(Review.id).desc(), func.avg(Review.rating).asc())
        .limit(10)
    ).all()
    return {
        'total': total or 0,
        'unanswered': unanswered or 0,
        'negative': negative or 0,
        'manual': manual or 0,
        'published': published or 0,
        'average': round(float(average), 2) if average is not None else 0,
        'drafts': db.scalar(select(func.count()).select_from(Draft).join(Review).where(*filters)),
        'topics': topics,
        'problem_products': [{'id': row[0], 'name': row[1], 'brand': row[2], 'negative': row[3], 'average': round(float(row[4]), 1)} for row in problem_rows],
        'period_days': days,
    }


def question_intent(text):
    # Buyers often send only the vehicle and VIN, without asking "will it fit".
    if vehicle_identifiers(text) or oem_numbers(text):return 'совместимость'
    value = (text or '').lower().replace('ё', 'е')
    if re.search(r'входит|в комплекте|комплектуется', value):return 'комплектность'
    patterns = (
        ('крепление', r'держател|закрепител|креплен|как.*(?:закреп|крепить)|где.*(?:размест|постав|располож)'),
        ('применение', r'как (?:использ|примен)|инструкц|сколько нанос|разводит|дозиров'),
        ('совместимость', r'подойд|совмест|можно ли.*(?:авто|машин|модел|материал|поверхност)'),
        ('комплектность', r'комплект|входит|сколько штук|насадк|упаковк'),
        ('размер', r'размер|длин|ширин|высот|объ[её]м|вес'),
        ('состав', r'состав|материал|содержит|безопасен'),
        ('наличие', r'налич|когда будет|цвет|вариант|запах'),
    )
    for label, pattern in patterns:
        if re.search(pattern, value):
            return label
    return 'общее'


def safe_question_fallback(question, product, intent):
    if analyze_customer_message(question.text)['buyer_asks_about_money']:
        return official_support_reply()
    if intent == 'совместимость':
        # Quote only a short model designation from the customer's question; never echo a VIN.
        question_text = re.sub(r'\b[A-HJ-NPR-Z0-9]{17}\b', ' ', question.text or '', flags=re.I)
        match = re.search(r'\b(?:для|на|к)\s+([\w-]+(?:\s+[\w-]+){0,4})', question_text, re.I)
        model = ''
        if match:
            tokens = []
            for token in match.group(1).split():
                if token.casefold() in {'vin', 'вин', 'подойдет', 'подойдёт', 'подойдут', 'подходит', 'совместим', 'артикул'}:
                    break
                tokens.append(token)
            if tokens and any(any(char.isupper() or char.isdigit() for char in token) for token in tokens):
                model = ' '.join(tokens)
        subject = f'«{model}»' if model else 'указанной вами модели'
        product_name = (product.name or 'товара').strip()[:120]
        return (f'Здравствуйте! Спасибо за вопрос о том, подойдёт ли товар «{product_name}» для {subject}. '
                'По доступным подтверждённым сведениям нельзя достоверно установить применяемость этого артикула. '
                'Рекомендуем сверить её по каталогу производителя с учётом модели и двигателя.')
    return ('Здравствуйте! Спасибо за вопрос. В карточке товара пока недостаточно подтверждённых данных '
            'для точного ответа, поэтому не хотим вводить вас в заблуждение.')


def assess_question_answer(question, product, answer, sources=None):
    question_words = set(WORDS.findall(question.text.lower()))
    answer_words = set(WORDS.findall(answer.lower()))
    overlap = len(question_words & answer_words)
    issues, score = [], 30
    breakdown = {
        'safety': {'label': 'Безопасность', 'score': 30, 'max': 30},
        'relevance': {'label': 'Ответ по существу', 'score': 0, 'max': 25},
        'facts': {'label': 'Опора на факты', 'score': 0, 'max': 20},
        'clarity': {'label': 'Ясность', 'score': 0, 'max': 15},
        'courtesy': {'label': 'Дружелюбность', 'score': 0, 'max': 10},
    }
    if 60 <= len(answer) <= 900:
        score += 15; breakdown['clarity']['score'] = 15
    else:
        issues.append('ответ должен быть содержательным и без лишней длины')
    if answer.lower().startswith(('здравствуйте', 'добрый день')):
        score += 10; breakdown['courtesy']['score'] = 10
    else:
        issues.append('добавить дружелюбное приветствие')
    if overlap or any(word in answer.lower() for word in WORDS.findall(product.name.lower())):
        score += 25; breakdown['relevance']['score'] = 25
    else:
        issues.append('ответить именно на заданный вопрос')
    confirmed = [source for source in (sources or []) if source.get('verification_status') == 'VERIFIED']
    cautious = any(phrase in answer.lower() for phrase in ('недостаточно подтвержден', 'не хотим вводить', 'уточняем информац'))
    factual_restraint = not (sources or []) and not re.search(r'\b\d+(?:[.,]\d+)?\s*(?:мл|л|см|мм|мин|час|%|г|кг)\b|гарантир|точно подойд|абсолютно безопас', answer.lower())
    if confirmed or factual_restraint:
        score += 20; breakdown['facts']['score'] = 20
    elif cautious:
        score += 10; breakdown['facts']['score'] = 10
    else:
        issues.append('нужна опора на подтверждённые сведения о товаре')
    if ('аккумулятор' in product.name.lower() and 'клемм' in question.text.lower()
            and re.search(r'\b\d+(?:[.,]\d+)?\s*(?:в|вольт)\b', answer.lower())
            and not ('номинальн' in answer.lower() and
                     re.search(r'фактическ|заряд|измеренн', answer.lower()))):
        issues.append('отличить номинальное напряжение от фактического заряда на клеммах')
        score -= 20
        breakdown['relevance']['score'] = max(0, breakdown['relevance']['score'] - 20)
    intent = question_intent(question.text)
    return {'score': min(100, score), 'passed': score >= 90 and not issues, 'issues': issues, 'intent_label': intent, 'breakdown': breakdown, 'sources': sources or [], 'checked_at': now(), 'evaluator_version': QUALITY_EVALUATOR_VERSION}


async def generate_question_draft(db, question, instruction=''):
    question_started = time.monotonic()
    web_seconds = model_seconds = 0.0
    fallback_reason = ''
    if question.is_answered:
        raise ValueError('На вопрос уже есть ответ')
    product = db.get(Product, question.product_id)
    existing = db.scalar(select(Draft).where(Draft.review_id == question.id))
    expected_revision = existing.revision if existing else None
    verified_facts = trusted_product_facts(db, product)
    sources = [{'text': fact.get('text',''), 'source': fact.get('source','Карточка товара'), 'verification_status':'VERIFIED'} for fact in verified_facts]
    analysis = analyze_customer_message(question.text)
    intent = question_intent(question.text)
    assortment = assortment_question_answer(db,question,product,trusted_product_facts) if not analysis['buyer_asks_about_money'] else None
    if assortment:
        sources.extend(assortment['sources']);intent=assortment['intent']
    if not assortment and intent == 'применение' and not has_usage_instruction(verified_facts):
        web_started = time.monotonic()
        try:
            found = cached_web_instruction_sources(product) or await asyncio.wait_for(web_research.search_instructions(product, db), timeout=8)
            if found:
                remember_web_instruction_sources(product, found)
                db.commit()
                sources.extend([{**item, 'verification_status':'WEB_UNVERIFIED'} for item in found[:3]])
        except (ValueError, TimeoutError):
            pass
        finally:
            web_seconds = time.monotonic() - web_started
    examples = []
    previous = db.scalars(select(Review).where(Review.marketplace.like('%_question'), Review.product_id == question.product_id, Review.is_answered == True, Review.id != question.id).order_by(Review.created_at.desc()).limit(5))
    for item in previous:
        if item.existing_answer:
            examples.append({'question': item.text, 'answer': item.existing_answer})
    memories = [memory.text for memory in db.scalars(select(Memory).where(Memory.enabled == True)) if memory.scope in ('global', 'product:' + product.id, 'brand:' + product.brand, 'category:' + product.category)]
    messages = [
        {'role': 'system', 'content': (Path(__file__).parent / 'prompts/question_reply.md').read_text(encoding='utf-8') + '\n' + customer_reply_policy()},
        {'role': 'user', 'content': json.dumps({'question': question.text, 'internal_analysis': analyze_customer_message(question.text), 'question_type': intent, 'product': product.name, 'brand': product.brand, 'verified_facts': verified_facts, 'similar_previous_answers': examples, 'store_rules': memories, 'previous_answer': existing.text if existing else '', 'owner_instruction': instruction}, ensure_ascii=False)},
    ]
    candidates = []
    fitment = fitment_answer(db, question, product) if not assortment and intent == 'совместимость' and (re.search(r'фильтр|колодк|свеч|диск|амортиз|ремень|подшип|рычаг|радиатор|автомоб|стартер|генератор|суппорт|сцеплен|катушк', product.name, re.I) or vehicle_identifiers(question.text) or oem_numbers(question.text)) else None
    if fitment and fitment['status']=='NEEDS_CATALOG' and setting(db,'internet-fitment-enabled:'+question.wb_account_id,False):
        from .internet_fitment import research_question
        web_started=time.monotonic()
        research=await research_question(db,question,product)
        fitment=fitment_answer(db,question,db.get(Product,question.product_id))
        fitment['internet']=research
        if fitment['status']=='NEEDS_CATALOG':
            fitment['pending_reason']=research.get('message','Требуется проверка оригинального OEM по каталогу')
        fitment['sources']+=research.get('sources',[])
        web_seconds=time.monotonic()-web_started
    voltage = battery_voltage_answer(question, product)
    if voltage:
        sources.append(voltage['source'])
    uses_model = not analysis['buyer_asks_about_money'] and not voltage and not fitment and not assortment
    model_started = time.monotonic()
    try:
        candidates = [official_support_reply()] if analyze_customer_message(question.text)['buyer_asks_about_money'] else [fitment['text']] if fitment else [voltage['answer']] if voltage else [assortment['answer']] if assortment else await GenerationBudget().run(lambda: ollama.reply_variants(messages, MIN_AI_CANDIDATES))
    except Exception as error:
        fallback_reason = 'timeout' if isinstance(error, TimeoutError) else 'unavailable'
        candidates = []
    model_seconds = time.monotonic() - model_started if uses_model else 0.0
    checked = []
    for candidate in candidates:
        try:
            value = validate_reply(mask_vin(candidate))
            if not existing or value.strip() != existing.text.strip():
                checked.append((value, {'score':100 if fitment['status'] in ('FIT','ALTERNATIVE','UNAVAILABLE','NEEDS_VIN') else 0,'passed':fitment['status'] in ('FIT','ALTERNATIVE','UNAVAILABLE','NEEDS_VIN'),'issues':[] if fitment['status'] in ('FIT','ALTERNATIVE','UNAVAILABLE','NEEDS_VIN') else ['Нужно подтверждение применяемости по каталогу'],'sources':fitment['sources'],'intent_label':intent,'fitment':fitment} if fitment else assess_question_answer(question, product, value, sources)))
        except ValueError:
            continue
    passed_candidates = [item for item in checked if item[1]['passed']]
    if fitment and not analysis['buyer_asks_about_money']:
        reply = validate_reply(fitment['text'])
        quality = {'score':100 if fitment['status'] in ('FIT','ALTERNATIVE','UNAVAILABLE','NEEDS_VIN') else 0,'passed':fitment['status'] in ('FIT','ALTERNATIVE','UNAVAILABLE','NEEDS_VIN'),'issues':[] if fitment['status'] in ('FIT','ALTERNATIVE','UNAVAILABLE','NEEDS_VIN') else ['Нужна проверка по каталогу'],'sources':fitment['sources'],'intent_label':intent,'fitment':fitment}
        model = 'catalog-fitment-1.1'
    elif assortment and not analysis['buyer_asks_about_money']:
        reply = validate_reply(mask_vin(assortment['answer']))
        quality = assess_question_answer(question, product, reply, sources)
        # Exact catalog matches establish relevance even when Russian inflections differ.
        if assortment['product_ids'] and 'ответить именно на заданный вопрос' in quality['issues']:
            quality['issues'].remove('ответить именно на заданный вопрос')
            quality['score'] = min(100,quality['score']+25-quality['breakdown']['relevance']['score'])
            quality['breakdown']['relevance']['score'] = 25
            quality['passed'] = quality['score']>=90 and not quality['issues']
        quality['intent_label'] = assortment['intent']
        quality['related_product_ids'] = assortment['product_ids']
        model = 'catalog-assortment-1.0'
    elif passed_candidates:
        reply, quality = max(passed_candidates, key=lambda item: item[1]['score'])
        model = MODEL
        quality['selection_note'] = f'Выбран лучший из {len(passed_candidates)} проверенных вариантов'
    else:
        reply = validate_reply(safe_question_fallback(question, product, intent))
        quality = assess_question_answer(question, product, reply, sources)
        quality['selection_note'] = 'Использован безопасный ответ без неподтверждённых сведений'
        model = 'question-safety-1.0'
    route = 'fallback' if model.startswith('question-safety') else 'model' if uses_model else 'rules'
    quality['timing'] = {'total_seconds': round(time.monotonic() - question_started, 3), 'web_seconds': round(web_seconds, 3), 'model_seconds': round(model_seconds, 3), 'route': route, 'fallback_reason': (fallback_reason or 'quality') if route == 'fallback' else ''}
    quality['candidates_evaluated'] = len(checked)
    quality['analysis_summary'] = f'Определён тип вопроса: {intent}. Ответ проверен на соответствие вопросу, подтверждённым фактам и правилам безопасности.'
    db.refresh(question)
    if question.is_answered:
        raise ValueError('Вопрос изменился во время генерации')
    draft = db.scalar(select(Draft).where(Draft.review_id == question.id).execution_options(populate_existing=True))
    if (draft.revision if draft else None) != expected_revision:
        raise ValueError('Черновик изменён во время генерации')
    if draft:
        edit_draft(db, draft.id, reply, draft.revision, learn_preferences=False)
        draft.model, draft.quality = model, quality
    else:
        draft = Draft(id=str(uuid4()), review_id=question.id, text=reply, original=reply, model=model, quality=quality)
        db.add(draft)
    question.status = 'draft_ready' if quality['passed'] else 'manual_review'
    db.commit()
    return public(draft)


async def generate_draft(db, review_id, instruction=''):
    generation_started = time.monotonic()
    web_seconds = model_seconds = 0.0
    fallback_reason = ''
    if not setting(db, 'assistant_enabled', True):
        raise ValueError('Ассистент отключён владельцем')
    review = db.get(Review, review_id)
    if review and review.marketplace.endswith('_question'):
        return await generate_question_draft(db, review, instruction)
    if not review or review.is_answered:
        raise ValueError('Отзыв не найден или уже имеет ответ')
    if review.status == 'publishing':
        raise ValueError('Публикация уже выполняется')
    initial_draft = db.scalar(select(Draft).where(Draft.review_id == review_id))
    expected_revision = initial_draft.revision if initial_draft else None
    product = db.get(Product, review.product_id)
    verified_facts = trusted_product_facts(db, product)
    memories = [m.text for m in db.scalars(select(Memory).where(Memory.enabled == True)) if m.scope in ('global', 'product:' + product.id, 'brand:' + product.brand, 'category:' + product.category)]
    generation_revision = initial_draft.revision if initial_draft else 0
    rule_candidates = []
    for offset in range(3):
        candidate = rule_reply(review, product, generation_revision + offset, verified_facts)
        if candidate and candidate not in rule_candidates:
            rule_candidates.append(validate_reply(mask_vin(candidate)))
    rule_candidates = fresh_reply_variants(rule_candidates, initial_draft.text if initial_draft else '')
    reply = max(rule_candidates, key=lambda value: assess_reply(review, product, value, verified_facts)['score']) if rule_candidates else None
    previous_quality = (initial_draft.quality or assess_reply(review, product, initial_draft.text, verified_facts)) if initial_draft else None
    # "Generate again" must not re-run the same non-critical deterministic template.
    # Keep mandatory safety/return flows deterministic; ask the local model to improve ordinary replies.
    structured_intents = ('WRONG_ITEM', 'MISSING_PARTS', 'DAMAGED_ITEM', 'FITMENT_PROBLEM', 'DESCRIPTION_MISMATCH')
    forced_model = None
    comparison_texts = []
    candidates_evaluated = max(1, len(rule_candidates))
    selection_note = f'Выбран лучший из {len(rule_candidates)} проверенных вариантов' if len(rule_candidates) > 1 else 'Использован проверенный сценарий ответа'
    if instruction.strip() and review.risk == 'NORMAL' and review_intent(review.text) not in structured_intents:
        # Directed improvements should reach the model even when a local rule exists.
        reply = None
    elif initial_draft and reply and reply.strip() != initial_draft.text.strip():
        # Deterministic safety replies rotate too, so regeneration remains useful
        # even while the local model is unavailable.
        forced_model = 'safety-rules-1.1'
    elif initial_draft and review.risk == 'NORMAL' and review_intent(review.text) not in structured_intents:
        issues = (previous_quality or {}).get('issues') or ['сделать ответ заметно лучше и содержательнее']
        instruction = (instruction + ' ' if instruction else '') + 'Это повторная генерация. Не повторяй прежний черновик. Исправь замечания: ' + '; '.join(issues) + '.'
        if not review.text.strip() and review.rating >= 4:
            # There is no buyer text to analyse. A polished rotating answer is faster and
            # more reliable than asking the model to invent details that are not present.
            reply = positive_rating_reply(product, initial_draft.revision)
            forced_model = 'positive-rules-1.1'
        else:
            reply = None
    web_search_failed = False
    if reply and 'короче' in instruction.lower():
        if review.risk == 'HIGH':
            reply = 'Здравствуйте! Описанные симптомы требуют проверки тормозной системы специалистом до дальнейшей эксплуатации. По отзыву установить причину невозможно. Пожалуйста, обратитесь в сервис.'
        elif review.risk == 'CAUTION':
            reply = 'Здравствуйте! Причины скрипа бывают разными; по отзыву определить их нельзя. Рекомендуем проверить тормозную систему и установку в сервисе.'
        elif review.rating >= 4 and review_intent(review.text) == 'POSITIVE_EXPERIENCE' and not product_usage_reply(review, product, verified_facts):
            reply = 'Здравствуйте! Спасибо за высокую оценку! Рады, что аромат вам понравился. Пользуйтесь с удовольствием!'
        else:
            reply = product_usage_reply(review, product, verified_facts, generation_revision) or reply
    model = forced_model or 'safety-rules-1.0'
    web_sources = []
    if not reply:
        model = MODEL
        facts = trusted_product_facts(db, product)
        style = owner_style_profile(db, review.wb_account_id)
        style['learned_from_edits'] = setting(db, 'learned_edit_preferences:' + review.wb_account_id, {})
        style['owner_feedback'] = setting(db, 'reply_feedback:' + review.wb_account_id, {})
        examples = previous_answer_examples(db, review, product)
        comparison_texts = ([initial_draft.text] if initial_draft else []) + [example.get('answer', '') for example in examples]
        web_sources = []
        if not has_usage_instruction(facts) and needs_usage_instruction(review, product):
            web_sources = cached_web_instruction_sources(product)
            try:
                if not web_sources:
                    web_started = time.monotonic()
                    try:
                        web_sources = await asyncio.wait_for(web_research.search_instructions(product, db), timeout=8)
                    finally:
                        web_seconds += time.monotonic() - web_started
                    remember_web_instruction_sources(product, web_sources)
                    db.commit()
            except (ValueError, TimeoutError):
                web_sources = []
                web_search_failed = True
        messages = [
            {'role': 'system', 'content': (Path(__file__).parent / 'prompts/review_reply.md').read_text(encoding='utf-8') + '\n' + customer_reply_policy() + '\nПравила владельца: ' + json.dumps(memories, ensure_ascii=False)},
            {'role': 'user', 'content': json.dumps({'review_data': review.text, 'internal_analysis': analyze_customer_message(review.text, review.rating), 'detected_intent': review_intent(review.text), 'rating': review.rating, 'product': product.name, 'verified_facts': facts, 'verified_usage_instruction': usage_excerpt(facts), 'owner_style_profile': style, 'similar_previous_answers': examples, 'previous_draft': initial_draft.text if initial_draft else '', 'previous_quality_issues': (previous_quality or {}).get('issues', []), 'owner_edit_instruction': instruction}, ensure_ascii=False)}]
        best_reply, best_quality = None, {'score': -1, 'issues': []}
        valid_candidates = []
        attempt_messages = messages
        last_candidate = ''
        model_unavailable = False
        budget = GenerationBudget()
        model_started = time.monotonic()
        try:
            seed_candidates = await budget.run(lambda: ollama.reply_variants(messages, MIN_AI_CANDIDATES))
        except Exception as error:
            fallback_reason = 'timeout' if isinstance(error, TimeoutError) else 'unavailable'
            # A connection failure should immediately use the safe local path instead
            # of holding the browser open for several identical network retries.
            seed_candidates = []
            model_unavailable = True
        for attempt in range(MAX_GENERATION_ATTEMPTS):
            if model_unavailable:
                break
            try:
                last_candidate = seed_candidates[attempt] if attempt < len(seed_candidates) else await budget.run(lambda: ollama.chat(attempt_messages))
                checked_candidate = validate_reply(mask_vin(last_candidate))
                candidate_quality = assess_candidate(review, product, checked_candidate, [*comparison_texts, *valid_candidates], verified_facts)
                repeated = bool(initial_draft and checked_candidate.strip() == initial_draft.text.strip())
                if not repeated and candidate_quality['score'] >= best_quality['score']:
                    best_reply, best_quality = checked_candidate, candidate_quality
                if not repeated and candidate_quality.get('similarity', 0) < 0.86:
                    valid_candidates.append(checked_candidate)
                issues = list(candidate_quality['issues'])
                if repeated:
                    issues.append('написать новый текст, заметно отличающийся от предыдущего черновика')
                if len(valid_candidates) >= MIN_AI_CANDIDATES and best_quality['score'] >= MIN_DRAFT_QUALITY:
                    break
                if candidate_quality['score'] >= MIN_DRAFT_QUALITY:
                    issues.append('создать ещё один самостоятельный вариант, чтобы выбрать лучший')
            except TimeoutError:
                fallback_reason = 'timeout'
                # Keep the best checked candidate or the existing safe fallback.
                break
            except Exception as error:
                # Ollama may be temporarily unavailable or may produce a reply that
                # violates a hard rule. Retry with the concrete validation feedback.
                issues = [str(error) or 'ответ не прошёл проверку безопасности']
            if attempt < MAX_GENERATION_ATTEMPTS - 1:
                attempt_messages = messages + [
                    {'role': 'assistant', 'content': last_candidate},
                    {'role': 'user', 'content': f'Напиши новый безопасный ответ с качеством не ниже {MIN_DRAFT_QUALITY}/100. Исправь замечания: ' + '; '.join(issues or ['сделать ответ конкретнее']) + '. Не используй запрещённые обещания; давай советы по применению только из verified_usage_instruction и verified_facts. Верни только ответ.'},
                ]
        model_seconds = time.monotonic() - model_started
        fallback = validate_reply(safe_fallback_reply(review, product, generation_revision, trusted_product_facts(db, product)))
        fallback_quality = assess_reply(review, product, fallback, verified_facts)
        candidates_evaluated = len(valid_candidates)
        if best_reply is None or fallback_quality['score'] > best_quality['score']:
            reply, model = fallback, 'safety-fallback-1.2'
            selection_note = 'Локальная модель не дала более качественный безопасный вариант; выбран контекстный ответ'
        else:
            reply = best_reply
            selection_note = f'Выбран лучший из {max(1, len(valid_candidates))} безопасных вариантов'
        if initial_draft and reply.strip() == initial_draft.text.strip() and not review.text.strip() and review.rating >= 4:
            reply = f'Здравствуйте! Большое спасибо за высокую оценку! 😊 Нам очень приятно, что вы выбрали {product.name}. Надеемся, товар будет радовать вас при использовании. Будем рады видеть вас снова!'
    # Explicit forbidden-word preferences can be enforced even for safety templates.
    for memory in memories:
        if any(x in memory.lower() for x in ('не использ', 'не пиши')):
            for word in re.findall('[«“\"]([^»”\"]+)[»”\"]', memory):
                reply = re.sub(re.escape(word), '', reply, flags=re.I)
    try:
        reply = validate_reply(mask_vin(reply))
    except ValueError:
        # A local-model answer may be useful but still violate a hard safety rule.
        # Preserve the safety boundary while always giving the owner a reviewable draft.
        reply = validate_reply(safe_fallback_reply(review, product, generation_revision, trusted_product_facts(db, product)))
        model = 'safety-fallback-1.2'
    if web_search_failed:
        model += ' · без интернет-источников'
    quality = assess_reply(review, product, reply, verified_facts)
    quality['candidates_evaluated'] = candidates_evaluated
    quality['selection_note'] = selection_note
    quality['analysis_summary'] = f'Определена ситуация: {quality["intent_label"]}. Ответ проверен на безопасность, соответствие отзыву, полезность и повторы.'
    quality['sources'] = [{'text': f.get('text', ''), 'source': f.get('source', ''), 'verification_status': 'VERIFIED'} for f in trusted_product_facts(db, product)]
    quality['unverified_sources_count'] = len(web_sources)
    route = 'fallback' if model.startswith('safety-fallback') else 'model' if model.split(' · ')[0] == MODEL else 'rules'
    quality['timing'] = {'total_seconds': round(time.monotonic() - generation_started, 3), 'web_seconds': round(web_seconds, 3), 'model_seconds': round(model_seconds, 3), 'route': route, 'fallback_reason': (fallback_reason or 'quality') if route == 'fallback' else ''}
    # Generation may have awaited Ollama while another request published the review.
    db.refresh(review)
    if review.is_answered or review.status == 'publishing':
        raise ValueError('Отзыв изменился во время генерации')
    draft = db.scalar(select(Draft).where(Draft.review_id == review_id).execution_options(populate_existing=True))
    if (draft.revision if draft else None) != expected_revision:
        raise ValueError('Черновик изменён во время генерации. Правки сохранены; повторите запрос при необходимости')
    if draft:
        edit_draft(db, draft.id, reply, draft.revision)
        draft.model = model
        draft.quality = quality
    else:
        draft = Draft(id=str(uuid4()), review_id=review_id, text=reply, original=reply, model=model, quality=quality)
        db.add(draft)
    review.status = 'manual_review' if review.manual or not quality['passed'] or model.startswith('safety-fallback') else 'draft_ready'
    db.commit()
    return public(draft)


def edit_draft(db, draft_id, value, revision, actor_id='', learn_preferences=True):
    db.rollback()
    db.execute(sql_text('BEGIN IMMEDIATE' if IS_SQLITE else 'BEGIN'))
    review_id = db.scalar(select(Draft.review_id).where(Draft.id == draft_id))
    # Use the same lock order as publication: review first, then draft.
    review = db.scalar(select(Review).where(Review.id == review_id).with_for_update().execution_options(populate_existing=True))
    draft = db.scalar(select(Draft).where(Draft.id == draft_id).with_for_update().execution_options(populate_existing=True))
    if not draft or draft.revision != revision:
        raise ValueError('Черновик изменился. Обновите страницу')
    if not review or review.status == 'publishing' or review.is_answered:
        raise ValueError('Этот ответ уже публикуется или опубликован')
    value = validate_reply(value)
    old = draft.text
    draft.edits = draft.edits + [{'old': old, 'new': value, 'diff': '\n'.join(difflib.ndiff(old.splitlines(), value.splitlines())), 'at': now(), 'by': actor_id or 'automation'}]
    draft.text, draft.revision = value, draft.revision + 1
    if actor_id:
        draft.updated_by = actor_id
    product = db.get(Product, review.product_id)
    generation_timing = (draft.quality or {}).get('timing')
    if review.marketplace.endswith('_question'):
        sources = [{'text': fact.get('text',''), 'source': fact.get('source','Карточка товара'), 'verification_status':'VERIFIED'} for fact in trusted_product_facts(db, product)]
        draft.quality = assess_question_answer(review, product, value, sources)
    else:
        draft.quality = assess_reply(review, product, value, trusted_product_facts(db, product))
    if generation_timing:
        draft.quality = {**draft.quality, 'timing': generation_timing}
    if learn_preferences:
        learn_from_edit(db, draft, old, value)
    review.status = 'manual_review' if review.manual or not draft.quality['passed'] else 'draft_ready'
    db.commit()
    return public(draft)


def propose_publish(db, review_ids):
    ids = list(dict.fromkeys(review_ids))
    if not 1 <= len(ids) <= 100:
        raise ValueError('Выберите от 1 до 100 отзывов')
    items, excluded = [], []
    for rid in ids:
        review = db.get(Review, rid)
        draft = db.scalar(select(Draft).where(Draft.review_id == rid))
        if not review or not draft or review.is_answered or review.status == 'publishing':
            raise ValueError('Для каждого выбранного отзыва нужен доступный черновик')
        is_question = review.marketplace.endswith('_question')
        if is_question and (draft.quality or {}).get('fitment',{}).get('status') in ('NEEDS_CATALOG','CONFLICT'):
            raise ValueError('Сначала подтвердите подбор по каталогу в разделе Товары')
        if len(ids) > 1 and not is_question and (review.rating <= 3 or review.manual):
            excluded.append(rid)
            continue
        validate_reply(draft.text)
        items.append({'review_id': rid, 'draft_id': draft.id, 'revision': draft.revision, 'text': draft.text, 'rating': review.rating, 'manual': review.manual, 'kind': 'question' if is_question else 'review'})
        review.status = 'pending_confirmation'
    if not items:
        raise ValueError('Массовая публикация исключает 1–3★ и опасные отзывы. Проверьте и подтвердите каждый отдельно')
    action = Action(id=str(uuid4()), kind='publish', payload={'items': items, 'excluded': excluded, 'test_mode': setting(db, 'test_mode', True)})
    db.add(action)
    db.commit()
    return public(action)


async def confirm_action(db, action_id, manual_ack=False, actor_id=''):
    # Reserve the action and unique per-review publication slots atomically.
    db.rollback()
    db.execute(sql_text('BEGIN IMMEDIATE' if IS_SQLITE else 'BEGIN'))
    action = db.scalar(select(Action).where(Action.id == action_id).with_for_update().execution_options(populate_existing=True))
    if not action:
        raise ValueError('Действие не найдено')
    if action.status != 'pending':
        raise ValueError('Действие уже обработано или выполняется')
    if datetime.now(timezone.utc) - datetime.fromisoformat(action.created_at) > timedelta(minutes=30):
        raise ValueError('Подтверждение устарело. Создайте новое действие')
    if action.kind == 'memory':
        db.add(Memory(id=str(uuid4()), **action.payload))
        action.status, action.result = 'completed', {'saved': True, 'confirmed_by': actor_id, 'confirmed_at': now()}
        db.commit()
        return public(action)
    if setting(db, 'safe_mode', False):
        raise ValueError('SAFE MODE блокирует публикацию')
    test = setting(db, 'test_mode', True)
    if action.payload['test_mode'] != test:
        raise ValueError('Режим изменён после подготовки действия. Подготовьте новое подтверждение')
    if not test and not REAL_PUBLISH:
        raise ValueError('Реальная публикация не разрешена при запуске приложения')
    for item in sorted(action.payload['items'], key=lambda entry: entry['review_id']):
        review = db.scalar(select(Review).where(Review.id == item['review_id']).with_for_update().execution_options(populate_existing=True))
        draft = db.scalar(select(Draft).where(Draft.id == item['draft_id']).with_for_update().execution_options(populate_existing=True))
        if not review or not draft or draft.revision != item['revision'] or draft.text != item['text'] or review.is_answered or review.status == 'publishing':
            raise ValueError('Черновик или отзыв изменился; нужно новое подтверждение')
        if review.manual and not manual_ack:
            raise ValueError('Нужно отдельное подтверждение ручной проверки опасного отзыва')
        validate_reply(draft.text)
        if not test:
            if db.scalar(select(Publication).where(Publication.review_id == review.id)):
                raise ValueError('Для ответа уже выполнялась публикация. Сначала проверьте его в маркетплейсе')
            db.add(Publication(id=str(uuid4()), review_id=review.id, action_id=action.id, status='in_flight'))
            review.status = 'publishing'
    action.status = 'executing'
    db.commit()
    results = []
    for item in action.payload['items']:
        review = db.get(Review, item['review_id'])
        if test:
            review.status = 'manual_review' if review.manual else 'draft_ready'
            results.append({'review_id': review.id, 'status': 'SIMULATED', 'wb_write': 0})
        else:
            job = db.scalar(select(Publication).where(Publication.review_id == review.id))
            try:
                store = db.get(StoreProfile, review.wb_account_id)
                provider = provider_for(store) if store else None
                if not provider: raise ValueError('Провайдер магазина не поддерживается')
                await provider.publish(db, review, item['text'])
                review.status, review.is_answered, review.existing_answer = 'published', True, item['text']
                job.status = 'published'
                results.append({'review_id': review.id, 'status': 'published'})
            except ValueError:
                # Avoid duplicate replies when the marketplace accepted a request but the response was lost.
                review.status, job.status = 'manual_review', 'uncertain'
                results.append({'review_id': review.id, 'status': 'uncertain', 'message': 'Проверьте ответ в маркетплейсе. Автоповтор запрещён.'})
        db.commit()
    action.status = 'completed' if all(x['status'] in ('SIMULATED', 'published') for x in results) else 'needs_review'
    action.result = {'items': results, 'test_mode': test, 'confirmed_by': actor_id, 'confirmed_at': now()}
    db.commit()
    return public(action)
