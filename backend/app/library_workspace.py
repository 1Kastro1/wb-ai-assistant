"""Library views and bounded case rules; activation requires owner opt-in."""
import hashlib
import json
from pathlib import Path
from sqlalchemy import select, func
from .db import Job, Review, Draft, Product, Memory, Setting, ReviewLibraryAuditRow, now, set_setting, setting

GUIDANCE = (
    ('ответить именно на содержание', 'Отвечай на конкретные достоинства и каждое существенное замечание покупателя; общая благодарность не заменяет ответ по существу.'),
    ('содержательным', 'Дай краткий содержательный ответ: признай названный покупателем опыт и при жалобе предложи один уместный следующий шаг только по подтверждённым сведениям.'),
    ('не трактовать положительный', 'Не трактуй положительный опыт как жалобу. Благодари за отзыв и отмечай только названное достоинство без диагностики и возвратов.'),
    ('не приписывать опыт', 'Если покупатель ещё не использовал товар, не утверждай, что товар подошёл, работает или оправдал ожидания; поблагодари за оценку без выдуманного опыта.'),
)
DEFAULT_GUIDANCE = 'Перед ответом выдели существенные замечания и вопрос покупателя. Учитывай каждое из них по действующим правилам и подтверждённым данным; если сведений недостаточно, укажи, чего именно не хватает, без выдуманных причин и обещаний.'
CASE_COVERAGE = json.loads(Path(__file__).with_name('rule_case_coverage173.json').read_text(encoding='utf-8'))


def covered_case_rule(db, instructions):
    """Only explicitly audited whole policies prove coverage of case guidance.

    A changed/disabled policy or another audience never suppresses suggestions.
    No similarity score, buyer wording or model verdict can expand this list.
    """
    covered, policies = set(), []
    from .rule_duplicates import normalized_scope
    for rule in db.scalars(select(Memory).where(Memory.enabled == True)):
        if rule.kind not in ('REVIEW_RULE', 'GLOBAL_RULE') or normalized_scope(rule.scope) != 'global':
            continue
        accepted = CASE_COVERAGE.get(hashlib.sha256(rule.text.encode('utf-8')).hexdigest(), [])
        if accepted:
            covered.update(accepted)
            policies.append((rule, set(accepted)))
    if instructions and set(instructions) <= covered:
        return max(policies, key=lambda pair: (len(pair[1] & set(instructions)), pair[0].id))[0]
    return None


def latest_job(db):
    return db.scalar(select(Job).where(Job.kind == 'review_library_audit').order_by(Job.created_at.desc(), Job.id.desc()).limit(1))


def category(issues):
    text = ' '.join(issues or []).lower()
    if any(marker in text for marker in ('тайм-аут','timeout','контекст модели','сервис недоступен')):
        return 'technical'
    if 'публикаци' in text or 'сверк' in text:
        return 'publication'
    if 'нет доступного' in text:
        return 'missing_data'
    return 'content'


def guidance(issues):
    if category(issues)!='content':
        return []
    instructions=[value for marker,value in GUIDANCE if marker in ' '.join(issues or []).lower()]
    return instructions or [DEFAULT_GUIDANCE]


def suggest_case_rule(db, row, review):
    """Buyer content never becomes an instruction or an automatic approval."""
    if row.status != 'attention' or not review or category(row.issues) != 'content':
        return None
    instructions = guidance(row.issues)
    if not instructions:
        return None
    covered = covered_case_rule(db, instructions)
    if covered:
        return covered
    from .rule_duplicates import try_rules_lock, duplicate_rule
    if not try_rules_lock(db):
        return None
    # A UI writer may have committed new coverage just before the lock.
    covered = covered_case_rule(db, instructions)
    if covered:
        return covered
    identity = hashlib.sha256((review.product_id + ':' + '\n'.join(instructions)).encode()).hexdigest()[:28]
    mid = 'case-rule-' + identity
    rule = db.get(Memory, mid)
    if rule is None:
        candidate = Memory(text='Разбор сложного отзыва. ' + ' '.join(instructions),
                           kind='REVIEW_RULE', scope='product:' + review.product_id)
        duplicate = duplicate_rule(db, candidate)
        if duplicate:
            return duplicate
        # A bounded set of editorial instructions, never raw buyer/model prose.
        # Do not grow a product policy indefinitely when retries cannot resolve it.
        if (db.scalar(select(func.count()).select_from(Memory).where(
                Memory.id.startswith('case-rule-'), Memory.scope=='product:'+review.product_id)) or 0) >= 5:
            return None
        rule = Memory(id=mid, text='Разбор сложного отзыва. ' + ' '.join(instructions),
                      kind='REVIEW_RULE', scope='product:' + review.product_id, enabled=False)
        db.add(rule)
        db.flush()
        set_setting(db, 'memory-meta:' + mid, {'created_at': now(), 'created_by_name': 'Автоматический разбор',
            'automatic_suggestion': True, 'source_review_id': review.id})
    meta = db.get(Setting, 'memory-meta:' + mid)
    info = dict(meta.value or {}) if meta else {}
    if info.get('duplicate_of'):
        return db.get(Memory, info['duplicate_of'])
    if (setting(db, 'auto_case_rules_enabled', False) and not rule.enabled
            and info.get('automatic_suggestion') and not info.get('updated_at')
            and not info.get('auto_activated_at')):
        from .rule_change_recheck import snapshot, rule_changed
        before = snapshot(rule)
        rule.enabled = True
        db.flush()
        rule_changed(db, before, snapshot(rule))
        set_setting(db, 'memory-meta:' + mid, {**info, 'auto_activated_at':now(),
            'reason':'owner_authorized_case_learning', 'requires_fresh_verification':True})
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
        'technical_stopped':values.get('technical_attention',0),
        'checked': verified + values.get('attention', 0) + values.get('removed', 0)+values.get('technical_attention',0)}


def cases(db, view='attention', offset=0, limit=20):
    job = latest_job(db)
    if not job:
        return {'items': [], 'total': 0, 'offset': offset, 'limit': limit}
    states = {'attention': ('attention',), 'retry': ('retry', 'blocked','technical_attention'),
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
        rule = None
        if instructions and row.status=='attention' and category(row.issues)=='content':
            rule = covered_case_rule(db, instructions) or db.get(Memory, mid)
            info = setting(db, 'memory-meta:' + rule.id, {}) if rule else {}
            if info.get('duplicate_of'):
                rule = db.get(Memory, info['duplicate_of'])
        items.append({'review_id': review.id, 'store_id': review.wb_account_id, 'marketplace': review.marketplace,
            'product': product.name if product else 'Товар не найден', 'review_text': review.text, 'rating': review.rating,
            'answer': review.existing_answer if review.is_answered else draft.text if draft else '',
            'status': row.status, 'category': 'technical' if row.status=='technical_attention' else category(row.issues), 'issues': row.issues,
            'attempts': row.attempts, 'retry_at': row.retry_at, 'updated_at': row.updated_at,
            'suggestion': {'id': rule.id, 'text': rule.text, 'scope': rule.scope, 'enabled': rule.enabled} if rule else None})
    return {'items': items, 'total': total, 'offset': offset, 'limit': limit}
