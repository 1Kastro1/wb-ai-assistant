import difflib
import json
import re
from collections import Counter
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, case, text as sql_text
from .db import *
from .safety import rule_reply, review_intent, safe_fallback_reply, validate_reply, mask_vin
from .integrations import ollama, wb, web_research
from .config import MODEL, REAL_PUBLISH


WORDS = re.compile(r'[а-яё0-9]{4,}', re.I)
MIN_DRAFT_QUALITY = 96
MAX_GENERATION_ATTEMPTS = 5
MIN_AI_CANDIDATES = 3
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


def needs_usage_instruction(review):
    if review_intent(review.text) == 'NO_RESULT':
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
    return [
        {'title': f.get('title', ''), 'snippet': f.get('text', ''), 'url': f.get('source', ''), 'host': f.get('host', ''), 'match_score': f.get('match_score', 0)}
        for f in (product.facts or [])
        if f.get('verification_status') == 'WEB_UNVERIFIED' and f.get('source')
    ][:5]


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


def reviews_search(db, q='', unanswered=False, max_rating=5, days=0, product_id='', limit=50, offset=0, account_id='', attention=False):
    query = select(Review).join(Product).where(Review.rating <= max_rating)
    if q:
        query = query.where((Review.text.contains(q, autoescape=True)) | Product.name.contains(q, autoescape=True) | Product.brand.contains(q, autoescape=True))
    if unanswered:
        query = query.where(Review.is_answered == False)
    if attention:
        query = query.where((Review.rating <= 3) | (Review.manual == True) | (Review.risk != 'NORMAL'))
    if days:
        start = datetime.now(timezone(timedelta(hours=3))).replace(hour=0, minute=0, second=0, microsecond=0) if days == 1 else datetime.now(timezone.utc) - timedelta(days=days)
        query = query.where(func.julianday(Review.created_at) >= func.julianday(start.isoformat()))
    if product_id:
        query = query.where(Review.product_id == product_id)
    if account_id:
        query = query.where(Review.wb_account_id == account_id)
    rows = db.scalars(query.order_by(Review.created_at.desc(), Review.id).offset(max(0, offset)).limit(min(200, max(1, limit)))).all()
    result = []
    for row in rows:
        item = public(row)
        item['product'] = public(db.get(Product, row.product_id))
        draft = db.scalar(select(Draft).where(Draft.review_id == row.id))
        item['draft'] = public(draft) if draft else None
        result.append(item)
    return result


def reviews_page(db, **filters):
    limit = min(200, max(1, int(filters.get('limit', 50))))
    offset = max(0, int(filters.get('offset', 0)))
    rows = reviews_search(db, **{**filters, 'limit': limit, 'offset': offset})
    count_query = select(func.count()).select_from(Review).join(Product).where(Review.rating <= filters.get('max_rating', 5))
    if filters.get('q'):
        q = filters['q']
        count_query = count_query.where((Review.text.contains(q, autoescape=True)) | Product.name.contains(q, autoescape=True) | Product.brand.contains(q, autoescape=True))
    if filters.get('unanswered'):
        count_query = count_query.where(Review.is_answered == False)
    if filters.get('attention'):
        count_query = count_query.where((Review.rating <= 3) | (Review.manual == True) | (Review.risk != 'NORMAL'))
    if filters.get('product_id'):
        count_query = count_query.where(Review.product_id == filters['product_id'])
    if filters.get('account_id'):
        count_query = count_query.where(Review.wb_account_id == filters['account_id'])
    if filters.get('days'):
        count_query = count_query.where(func.julianday(Review.created_at) >= func.julianday((datetime.now(timezone.utc) - timedelta(days=filters['days'])).isoformat()))
    total = db.scalar(count_query) or 0
    return {'items': rows, 'total': total, 'limit': limit, 'offset': offset, 'has_more': offset + len(rows) < total}


def assess_reply(review, product, reply):
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
    intent = review_intent(review.text)
    if intent == 'WRONG_ITEM':
        if 'возврат' in lowered or 'вернуть' in lowered:
            score += 18
            breakdown['resolution']['score'] = 18
        else:
            issues.append('объяснить возможность возврата неверного товара')
    elif intent == 'NO_RESULT':
        if any(x in lowered for x in ('инструкц', 'нанес', 'обработ', 'примен')):
            score += 15
            breakdown['resolution']['score'] = 15
        else:
            issues.append('дать безопасную инструкцию по применению')
    else:
        score += 10
        breakdown['resolution']['score'] = 10
    if any(x in lowered for x in ('напишите продавцу', 'свяжитесь с продавцом', 'обратитесь к продавцу')):
        score = 0
        breakdown['safety']['score'] = 0
        issues.append('не отправлять покупателя писать продавцу')
    if reply.strip().lower() in ('спасибо за отзыв!', 'спасибо за отзыв.', 'спасибо!'):
        score = min(score, 35)
        issues.append('слишком общий ответ')
    intent = review_intent(review.text)
    return {
        'score': min(100, score), 'passed': score >= MIN_DRAFT_QUALITY and not issues,
        'issues': list(dict.fromkeys(issues)), 'breakdown': breakdown,
        'intent': intent, 'intent_label': INTENT_LABELS.get(intent, INTENT_LABELS['GENERAL']),
        'checked_at': now(),
    }


def assess_candidate(review, product, reply, comparisons=()):
    """Apply the normal quality gate and penalise near-copies of earlier answers."""
    quality = assess_reply(review, product, reply)
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
    filters = []
    if days:
        filters.append(func.julianday(Review.created_at) >= func.julianday((datetime.now(timezone.utc) - timedelta(days=days)).isoformat()))
    if account_id:
        filters.append(Review.wb_account_id == account_id)
    drafts = list(db.scalars(select(Draft).join(Review).where(*filters)))
    changed = False
    for draft in drafts:
        if not draft.quality:
            review = db.get(Review, draft.review_id)
            product = db.get(Product, review.product_id)
            draft.quality = assess_reply(review, product, draft.text)
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
    filters = []
    if days:
        filters.append(func.julianday(Review.created_at) >= func.julianday((datetime.now(timezone.utc) - timedelta(days=days)).isoformat()))
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
    topic_where = []
    topic_params = {}
    if days:
        topic_where.append('julianday(r.created_at) >= julianday(:topic_start)')
        topic_params['topic_start'] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if product_id:
        topic_where.append('r.product_id = :topic_product_id')
        topic_params['topic_product_id'] = product_id
    if account_id:
        topic_where.append('r.wb_account_id = :topic_account_id')
        topic_params['topic_account_id'] = account_id
    topic_sql = 'SELECT j.value, COUNT(*) FROM reviews AS r, json_each(r.topics) AS j'
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


async def generate_draft(db, review_id, instruction=''):
    review = db.get(Review, review_id)
    if not review or review.is_answered:
        raise ValueError('Отзыв не найден или уже имеет ответ')
    if review.status == 'publishing':
        raise ValueError('Публикация уже выполняется')
    initial_draft = db.scalar(select(Draft).where(Draft.review_id == review_id))
    expected_revision = initial_draft.revision if initial_draft else None
    product = db.get(Product, review.product_id)
    memories = [m.text for m in db.scalars(select(Memory).where(Memory.enabled == True)) if m.scope in ('global', 'product:' + product.id, 'brand:' + product.brand, 'category:' + product.category)]
    generation_revision = initial_draft.revision if initial_draft else 0
    reply = rule_reply(review, product, generation_revision)
    previous_quality = (initial_draft.quality or assess_reply(review, product, initial_draft.text)) if initial_draft else None
    # "Generate again" must not re-run the same non-critical deterministic template.
    # Keep mandatory safety/return flows deterministic; ask the local model to improve ordinary replies.
    structured_intents = ('WRONG_ITEM', 'MISSING_PARTS', 'DAMAGED_ITEM', 'FITMENT_PROBLEM')
    forced_model = None
    comparison_texts = []
    candidates_evaluated = 1
    selection_note = 'Использован проверенный сценарий ответа'
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
        else:
            reply = 'Жаль, что аромат показался слабым. EIKOSHA — мягкий аромат, его восприятие индивидуально. Попробуйте размещение ближе к потоку воздуха по инструкции модели.'
    model = forced_model or 'safety-rules-1.0'
    if not reply:
        model = MODEL
        facts = [f for f in product.facts if f.get('verification_status') == 'VERIFIED']
        style = owner_style_profile(db, review.wb_account_id)
        style['learned_from_edits'] = setting(db, 'learned_edit_preferences:' + review.wb_account_id, {})
        examples = previous_answer_examples(db, review, product)
        comparison_texts = ([initial_draft.text] if initial_draft else []) + [example.get('answer', '') for example in examples]
        web_sources = []
        if not has_usage_instruction(facts) and needs_usage_instruction(review):
            web_sources = cached_web_instruction_sources(product)
            try:
                if not web_sources:
                    web_sources = await web_research.search_instructions(product, db)
                    remember_web_instruction_sources(product, web_sources)
                    db.commit()
            except ValueError:
                web_sources = []
                web_search_failed = True
        messages = [
            {'role': 'system', 'content': (Path(__file__).parent / 'prompts/review_reply.md').read_text(encoding='utf-8') + '\nПравила владельца: ' + json.dumps(memories, ensure_ascii=False)},
            {'role': 'user', 'content': json.dumps({'review_data': review.text, 'detected_intent': review_intent(review.text), 'rating': review.rating, 'product': product.name, 'verified_facts': facts, 'web_instruction_sources': web_sources, 'owner_style_profile': style, 'similar_previous_answers': examples, 'previous_draft': initial_draft.text if initial_draft else '', 'previous_quality_issues': (previous_quality or {}).get('issues', []), 'owner_edit_instruction': instruction}, ensure_ascii=False)}]
        best_reply, best_quality = None, {'score': -1, 'issues': []}
        valid_candidates = []
        attempt_messages = messages
        last_candidate = ''
        model_unavailable = False
        try:
            seed_candidates = await ollama.reply_variants(messages, MIN_AI_CANDIDATES)
        except Exception:
            # A connection failure should immediately use the safe local path instead
            # of holding the browser open for several identical network retries.
            seed_candidates = []
            model_unavailable = True
        for attempt in range(MAX_GENERATION_ATTEMPTS):
            if model_unavailable:
                break
            try:
                last_candidate = seed_candidates[attempt] if attempt < len(seed_candidates) else await ollama.chat(attempt_messages)
                checked_candidate = validate_reply(mask_vin(last_candidate))
                candidate_quality = assess_candidate(review, product, checked_candidate, [*comparison_texts, *valid_candidates])
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
            except Exception as error:
                # Ollama may be temporarily unavailable or may produce a reply that
                # violates a hard rule. Retry with the concrete validation feedback.
                issues = [str(error) or 'ответ не прошёл проверку безопасности']
            if attempt < MAX_GENERATION_ATTEMPTS - 1:
                attempt_messages = messages + [
                    {'role': 'assistant', 'content': last_candidate},
                    {'role': 'user', 'content': f'Напиши новый безопасный ответ с качеством не ниже {MIN_DRAFT_QUALITY}/100. Исправь замечания: ' + '; '.join(issues or ['сделать ответ конкретнее']) + '. Не используй запрещённые обещания и не направляй покупателя к продавцу. Верни только ответ.'},
                ]
        fallback = validate_reply(safe_fallback_reply(review, product, generation_revision))
        fallback_quality = assess_reply(review, product, fallback)
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
        reply = validate_reply(safe_fallback_reply(review, product, generation_revision))
        model = 'safety-fallback-1.2'
    if web_search_failed:
        model += ' · без интернет-источников'
    quality = assess_reply(review, product, reply)
    quality['candidates_evaluated'] = candidates_evaluated
    quality['selection_note'] = selection_note
    quality['analysis_summary'] = f'Определена ситуация: {quality["intent_label"]}. Ответ проверен на безопасность, соответствие отзыву, полезность и повторы.'
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
    review.status = 'manual_review' if review.manual else 'draft_ready'
    db.commit()
    return public(draft)


def edit_draft(db, draft_id, value, revision):
    db.rollback()
    db.execute(sql_text('BEGIN IMMEDIATE'))
    draft = db.get(Draft, draft_id, populate_existing=True)
    if not draft or draft.revision != revision:
        raise ValueError('Черновик изменился. Обновите страницу')
    review = db.get(Review, draft.review_id, populate_existing=True)
    if review.status == 'publishing' or review.is_answered:
        raise ValueError('Этот ответ уже публикуется или опубликован')
    value = validate_reply(value)
    old = draft.text
    draft.edits = draft.edits + [{'old': old, 'new': value, 'diff': '\n'.join(difflib.ndiff(old.splitlines(), value.splitlines())), 'at': now()}]
    draft.text, draft.revision = value, draft.revision + 1
    product = db.get(Product, review.product_id)
    draft.quality = assess_reply(review, product, value)
    learn_from_edit(db, draft, old, value)
    review.status = 'manual_review' if review.manual else 'draft_ready'
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
        if len(ids) > 1 and (review.rating <= 3 or review.manual):
            excluded.append(rid)
            continue
        validate_reply(draft.text)
        items.append({'review_id': rid, 'draft_id': draft.id, 'revision': draft.revision, 'text': draft.text, 'rating': review.rating, 'manual': review.manual})
        review.status = 'pending_confirmation'
    if not items:
        raise ValueError('Массовая публикация исключает 1–3★ и опасные отзывы. Проверьте и подтвердите каждый отдельно')
    action = Action(id=str(uuid4()), kind='publish', payload={'items': items, 'excluded': excluded, 'test_mode': setting(db, 'test_mode', True)})
    db.add(action)
    db.commit()
    return public(action)


async def confirm_action(db, action_id, manual_ack=False):
    # Reserve the action and unique per-review publication slots atomically.
    db.rollback()
    db.execute(sql_text('BEGIN IMMEDIATE'))
    action = db.get(Action, action_id, populate_existing=True)
    if not action:
        raise ValueError('Действие не найдено')
    if action.status != 'pending':
        raise ValueError('Действие уже обработано или выполняется')
    if datetime.now(timezone.utc) - datetime.fromisoformat(action.created_at) > timedelta(minutes=30):
        raise ValueError('Подтверждение устарело. Создайте новое действие')
    if action.kind == 'memory':
        db.add(Memory(id=str(uuid4()), **action.payload))
        action.status, action.result = 'completed', {'saved': True}
        db.commit()
        return public(action)
    if setting(db, 'safe_mode', False):
        raise ValueError('SAFE MODE блокирует публикацию')
    test = setting(db, 'test_mode', True)
    if action.payload['test_mode'] != test:
        raise ValueError('Режим изменён после подготовки действия. Подготовьте новое подтверждение')
    if not test and not REAL_PUBLISH:
        raise ValueError('Реальная публикация не разрешена при запуске приложения')
    for item in action.payload['items']:
        draft, review = db.get(Draft, item['draft_id']), db.get(Review, item['review_id'])
        if not draft or draft.revision != item['revision'] or draft.text != item['text'] or review.is_answered or review.status == 'publishing':
            raise ValueError('Черновик или отзыв изменился; нужно новое подтверждение')
        if review.manual and not manual_ack:
            raise ValueError('Нужно отдельное подтверждение ручной проверки опасного отзыва')
        validate_reply(draft.text)
        if not test:
            if db.scalar(select(Publication).where(Publication.review_id == review.id)):
                raise ValueError('Для отзыва уже выполнялась публикация. Сначала сверка с WB')
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
                await wb.publish(db, review, item['text'])
                review.status, review.is_answered, review.existing_answer = 'published', True, item['text']
                job.status = 'published'
                results.append({'review_id': review.id, 'status': 'published'})
            except ValueError:
                # WB has no idempotency key. Ambiguous failures never retry a POST.
                review.status, job.status = 'manual_review', 'uncertain'
                results.append({'review_id': review.id, 'status': 'uncertain', 'message': 'Проверьте ответ в WB. Автоповтор запрещён.'})
        db.commit()
    action.status = 'completed' if all(x['status'] in ('SIMULATED', 'published') for x in results) else 'needs_review'
    action.result = {'items': results, 'test_mode': test}
    db.commit()
    return public(action)
