"""Read-only library views and deduplicated, inactive case-rule suggestions."""
import hashlib
from sqlalchemy import select, func
from .db import Job, Review, Draft, Product, Memory, Setting, ReviewLibraryAuditRow, now, set_setting

GUIDANCE = (
    ('ответить именно на содержание', 'Отвечай на конкретные достоинства и каждое существенное замечание покупателя; общая благодарность не заменяет ответ по существу.'),
    ('содержательным', 'Дай краткий содержательный ответ: признай названный покупателем опыт и при жалобе предложи один уместный следующий шаг только по подтверждённым сведениям.'),
    ('не трактовать положительный', 'Не трактуй положительный опыт как жалобу. Благодари за отзыв и отмечай только названное достоинство без диагностики и возвратов.'),
    ('не приписывать опыт', 'Если покупатель ещё не использовал товар, не утверждай, что товар подошёл, работает или оправдал ожидания; поблагодари за оценку без выдуманного опыта.'),
)


def latest_job(db):
    return db.scalar(select(Job).where(Job.kind == 'review_library_audit').order_by(Job.created_at.desc(), Job.id.desc()).limit(1))


def category(issues):
    text = ' '.join(issues or []).lower()
    if 'публикаци' in text or 'сверк' in text:
        return 'publication'
    if 'нет доступного' in text:
        return 'missing_data'
    return 'content'


def guidance(issues):
    if category(issues)!='content':
        return []
    instructions=[value for marker,value in GUIDANCE if marker in ' '.join(issues or []).lower()]
    return instructions or ['Перед ответом выдели существенные замечания и вопрос покупателя. Учитывай каждое из них по действующим правилам и подтверждённым данным; если сведений недостаточно, укажи, чего именно не хватает, без выдуманных причин и обещаний.']


def suggest_case_rule(db, row, review):
    """Buyer content never becomes an instruction or an automatic approval."""
    if row.status != 'attention' or not review or category(row.issues) != 'content':
        return None
    instructions = guidance(row.issues)
    if not instructions:
        return None
    identity = hashlib.sha256((review.product_id + ':' + '\n'.join(instructions)).encode()).hexdigest()[:28]
    mid = 'case-rule-' + identity
    rule = db.get(Memory, mid)
    if rule is None:
        rule = Memory(id=mid, text='Разбор сложного отзыва. ' + ' '.join(instructions),
                      kind='REVIEW_RULE', scope='product:' + review.product_id, enabled=False)
        db.add(rule)
        db.flush()
        set_setting(db, 'memory-meta:' + mid, {'created_at': now(), 'created_by_name': 'Автоматический разбор',
            'automatic_suggestion': True, 'source_review_id': review.id})
    return rule


def backfill_suggestions(db):
    job = latest_job(db)
    if not job:
        return 0
    rows = db.execute(select(ReviewLibraryAuditRow, Review).join(Review, Review.id == ReviewLibraryAuditRow.review_id)
        .where(ReviewLibraryAuditRow.job_id == job.id, ReviewLibraryAuditRow.status == 'attention')).all()
    count = sum(bool(suggest_case_rule(db, row, review)) for row, review in rows)
    db.commit()
    return count


def counts(db, job_id):
    values = dict(db.execute(select(ReviewLibraryAuditRow.status, func.count()).where(
        ReviewLibraryAuditRow.job_id == job_id).group_by(ReviewLibraryAuditRow.status)).all())
    verified = sum(values.get(key, 0) for key in ('verified', 'verified_reference'))
    return {'verified': verified, 'attention': values.get('attention', 0),
        'retry': values.get('retry', 0), 'blocked': values.get('blocked', 0),
        'checked': verified + values.get('attention', 0) + values.get('removed', 0)}


def cases(db, view='attention', offset=0, limit=20):
    job = latest_job(db)
    if not job:
        return {'items': [], 'total': 0, 'offset': offset, 'limit': limit}
    states = {'attention': ('attention',), 'retry': ('retry', 'blocked'),
              'verified': ('verified', 'verified_reference'), 'history': ()}[view]
    query = select(ReviewLibraryAuditRow, Review, Product, Draft).join(Review, Review.id == ReviewLibraryAuditRow.review_id)
    query = query.outerjoin(Product, Product.id == Review.product_id).outerjoin(Draft, Draft.review_id == Review.id)
    query = query.where(ReviewLibraryAuditRow.job_id == job.id)
    if states:
        query = query.where(ReviewLibraryAuditRow.status.in_(states))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(ReviewLibraryAuditRow.updated_at.desc(), ReviewLibraryAuditRow.review_id)
        .offset(offset).limit(limit)).all()
    items = []
    for row, review, product, draft in rows:
        instructions = guidance(row.issues)
        mid = 'case-rule-' + hashlib.sha256((review.product_id + ':' + '\n'.join(instructions)).encode()).hexdigest()[:28]
        rule = db.get(Memory, mid) if instructions and row.status=='attention' and category(row.issues)=='content' else None
        items.append({'review_id': review.id, 'store_id': review.wb_account_id, 'marketplace': review.marketplace,
            'product': product.name if product else 'Товар не найден', 'review_text': review.text, 'rating': review.rating,
            'answer': review.existing_answer if review.is_answered else draft.text if draft else '',
            'status': row.status, 'category': category(row.issues), 'issues': row.issues,
            'attempts': row.attempts, 'retry_at': row.retry_at, 'updated_at': row.updated_at,
            'suggestion': {'id': rule.id, 'text': rule.text, 'scope': rule.scope, 'enabled': rule.enabled} if rule else None})
    return {'items': items, 'total': total, 'offset': offset, 'limit': limit}
