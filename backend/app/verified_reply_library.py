"""One reply body, independent approvals; similar examples never confer approval."""
from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import re
import time
import unicodedata
from sqlalchemy import select, delete, func, case, and_
from sqlalchemy.orm import load_only
from .db import VerifiedReplyText,VerifiedReplyContext,Setting,Review,Product,IS_SQLITE,setting

REVIEW_MARKETS = ('wb', 'ozon', 'yandex')
EDITS_KEY = 'review-template-edits'


def _review_market(proof):
    return func.coalesce(func.nullif(proof.source_marketplace, ''), Review.marketplace)


def _iso(timestamp):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _review_categories(review):
    from .review_context import buyer_message
    from .reply_policy import analyze_customer_message, COMPLAINT_LABELS
    from .product_guidance import untested_review

    message = buyer_message(review).strip()
    if not message:
        return ['Оценка без текста']
    analysis = analyze_customer_message(message, review.rating)
    if analysis['complaints']:
        return [COMPLAINT_LABELS.get(key, 'Другая претензия').capitalize() for key in analysis['complaints']]
    if analysis['is_question']:
        return ['Вопрос в отзыве']
    if untested_review(message):
        return ['Первые впечатления']
    return ['Положительный отзыв' if analysis['sentiment'] == 'positive'
            else 'Негативный отзыв' if analysis['sentiment'] == 'negative' else 'Общий отзыв']


def _template_aliases(edits):
    return {alias: key for key, edit in edits.items()
            for alias in [*edit.get('aliases', ()), text_id(edit['text'])] if alias != key}


def saved_review_templates(db, market='', search='', offset=0, limit=20):
    from .safety import mask_vin

    edits = setting(db, EDITS_KEY, {})
    proof = VerifiedReplyContext
    marketplace = _review_market(proof)
    aliases = _template_aliases(edits)
    template = case(aliases, value=proof.template_id, else_=proof.template_id) if aliases else proof.template_id
    grouped = select(template.label('template_id'), func.count().label('contexts'), func.max(proof.verified_at).label('verified_at'),
        *(func.max(case((marketplace == value, 1), else_=0)).label(value) for value in REVIEW_MARKETS)
    ).outerjoin(Review, and_(proof.source_marketplace == '', proof.source_review_id == Review.id)
    ).where(marketplace.in_(REVIEW_MARKETS)).group_by(template).subquery()
    text = (case({key: edit['text'] for key, edit in edits.items()},
                 value=VerifiedReplyText.id, else_=VerifiedReplyText.text) if edits else VerifiedReplyText.text)
    query = select(VerifiedReplyText.id, text.label('text'), grouped.c.contexts, grouped.c.verified_at,
        *(grouped.c[value] for value in REVIEW_MARKETS)).join(grouped, grouped.c.template_id == VerifiedReplyText.id)
    if market:
        query = query.where(grouped.c[market] == 1)
    if search.strip():
        if IS_SQLITE:
            query = query.where(func.unicode_casefold(text).contains(search.strip().casefold(), autoescape=True))
        else:
            query = query.where(func.lower(text).contains(search.strip().lower(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(grouped.c.verified_at.desc(), VerifiedReplyText.id).offset(offset).limit(limit)).mappings().all()
    counts = {row['id']: {'categories': set(), 'products': set(), 'ratings': set(), 'brands': set()} for row in rows}
    ids = set(counts) | {key for key, root in aliases.items() if root in counts}
    if ids:
        sources = select(proof.template_id, proof.source_review_id).where(
            proof.template_id.in_(ids), proof.source_review_id != '').distinct().subquery()
        examples = (select(sources.c.template_id, Review, Product.category, Product.brand)
                    .join(Review, Review.id == sources.c.source_review_id)
                    .outerjoin(Product, Product.id == Review.product_id)
                    .where(Review.marketplace.in_(REVIEW_MARKETS))
                    .options(load_only(Review.text, Review.sections, Review.rating)))
        for key, review, category, brand in db.execute(examples):
            fields = counts[aliases.get(key, key)]
            fields['categories'].update(_review_categories(review))
            if category:
                fields['products'].add(category)
            if brand := (brand or '').strip():
                fields['brands'].add(brand)
            if 1 <= review.rating <= 5:
                fields['ratings'].add(review.rating)
    return {'total': total, 'items': [
        {'id': row['id'], 'text': mask_vin(row['text']), 'contexts': row['contexts'],
         'marketplaces': [value for value in REVIEW_MARKETS if row[value]],
         'verified_at': _iso(row['verified_at']), 'revision': edits.get(row['id'], {}).get('revision', 0),
         **{key: sorted(values) for key, values in counts[row['id']].items() if key != 'brands'},
         'brands': sorted({brand.casefold(): brand for brand in sorted(counts[row['id']]['brands'], reverse=True)}.values(),
                          key=str.casefold)}
        for row in rows
    ]}


def text_id(answer):
    normalized=re.sub(r'\s+',' ',unicodedata.normalize('NFKC',answer)).strip().casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def insert():
    from sqlalchemy.dialects.sqlite import insert as sqlite
    from sqlalchemy.dialects.postgresql import insert as postgres
    return sqlite if IS_SQLITE else postgres


class TemplateConflict(ValueError):
    pass


def edit_template(db, template_id, answer, revision):
    from .reply_policy import has_reply_body
    from .safety import mask_vin, validate_reply

    answer = validate_reply(answer.strip())
    if answer != mask_vin(answer) or not has_reply_body(answer):
        raise ValueError('Введите текст ответа без VIN, FRAME и персональных данных.')
    db.execute(insert()(Setting).values(key=EDITS_KEY, value={}).on_conflict_do_nothing(index_elements=['key']))
    row = db.scalar(select(Setting).where(Setting.key == EDITS_KEY).with_for_update()
                    .execution_options(populate_existing=True))
    edits = row.value or {}
    proof = VerifiedReplyContext
    exists = db.scalar(select(proof.id).outerjoin(Review, Review.id == proof.source_review_id)
                       .where(proof.template_id == template_id, _review_market(proof).in_(REVIEW_MARKETS)).limit(1))
    original = db.get(VerifiedReplyText, template_id)
    aliases = _template_aliases(edits)
    if not exists or not original or template_id in aliases:
        raise LookupError('Шаблон не найден. Обновите список.')
    current = edits.get(template_id, {'revision': 0, 'text': original.text})
    if current['revision'] != revision:
        raise TemplateConflict('Шаблон изменился. Обновите список и повторите правку.')
    if answer == current['text']:
        return current
    target = text_id(answer)
    if target != template_id and aliases.get(target) != template_id and (
            target in aliases or db.get(VerifiedReplyText, target)):
        raise TemplateConflict('Шаблон с таким текстом уже есть в библиотеке.')
    previous_ids = set(current.get('aliases', ())) | {text_id(current['text'])}
    updated = {'text': answer, 'revision': revision + 1, 'aliases': sorted(previous_ids - {template_id, target})}
    row.value = {**edits, template_id: updated}
    return updated


def body(db,answer):
    tid=text_id(answer)
    db.execute(insert()(VerifiedReplyText).values(id=tid,text=answer)
        .on_conflict_do_nothing(index_elements=['id']))
    saved=db.get(VerifiedReplyText,tid)
    # Keep exact approved presentation without duplicating the reply body.
    patches=[[a,b,answer[c:d]] for tag,a,b,c,d in SequenceMatcher(None,saved.text,answer,autojunk=False).get_opcodes() if tag!='equal']
    return tid,patches


def render(answer,patches):
    for start,end,value in reversed(patches or []):
        if not 0<=start<=end<=len(answer):raise ValueError('Invalid template presentation')
        answer=answer[:start]+value+answer[end:]
    return answer


def remember(db,key,kind,answer,verdict,source='',marketplace='',verified_at=None):
    tid,patches=body(db,answer)
    cid=hashlib.sha256((key+':'+kind+':'+tid).encode()).hexdigest()
    values={'template_id':tid,'context_key':key,'kind':kind,'verdict':deepcopy(verdict),
        'presentation':patches,'source_review_id':source,'source_marketplace':marketplace,
        'verified_at':time.time() if verified_at is None else verified_at}
    db.execute(insert()(VerifiedReplyContext).values(id=cid,**values)
        .on_conflict_do_update(index_elements=['id'],set_=values))
    if kind!='legacy':
        db.execute(delete(VerifiedReplyContext).where(VerifiedReplyContext.context_key==key,
            VerifiedReplyContext.template_id==tid,VerifiedReplyContext.kind=='legacy'))
    return tid


def variants(db,key,kind):
    from .learned_reply_templates import MAX_AGE
    rows=db.execute(select(VerifiedReplyContext,VerifiedReplyText.text)
        .join(VerifiedReplyText,VerifiedReplyText.id==VerifiedReplyContext.template_id)
        .where(VerifiedReplyContext.context_key==key,VerifiedReplyContext.kind.in_((kind,'legacy')))
        .order_by(VerifiedReplyContext.verified_at.desc(),VerifiedReplyContext.id).limit(12)
        .execution_options(populate_existing=True)).all()
    result=[]
    for entry,text in rows:
        if entry.kind=='legacy' and time.time()-entry.verified_at>MAX_AGE:continue
        result.append({'answer':render(text,entry.presentation),'verdict':deepcopy(entry.verdict),
            'at':entry.verified_at,'source':entry.source_review_id,'template_id':entry.template_id,'durable':entry.kind!='legacy'})
    return result


def statistics(db):
    return {'unique_templates':db.scalar(select(func.count()).select_from(VerifiedReplyText)) or 0,
        'contexts':db.scalar(select(func.count()).select_from(VerifiedReplyContext)) or 0,
        'markets':dict(db.execute(select(VerifiedReplyContext.source_marketplace,func.count())
            .group_by(VerifiedReplyContext.source_marketplace)).all())}


def migrate_legacy(db):
    """Lossless transactional cache conversion; preserve expired proofs as inactive."""
    from .learned_reply_templates import _variants
    rows=list(db.scalars(select(Setting).where(Setting.key.startswith('verified-replies:')).order_by(Setting.key)))
    converted=0
    for row in rows:
        for key,entry in (row.value or {}).items():
            for item in _variants(entry,db):
                remember(db,key,'legacy',item['answer'],item.get('verdict') or {},
                    source=item.get('source',''),verified_at=float(item.get('at',0)))
                converted+=1
    # Bodies that lost a bounded context are still preserved as unapproved text.
    for row in db.scalars(select(Setting).where(Setting.key.startswith('verified-texts:'))):
        for item in (row.value or {}).values():
            if item.get('answer'):body(db,item['answer'])
    db.execute(delete(Setting).where(Setting.key.startswith('verified-replies:')))
    db.execute(delete(Setting).where(Setting.key.startswith('verified-texts:')))
    db.flush()
    return {**statistics(db),'converted_approvals':converted}
