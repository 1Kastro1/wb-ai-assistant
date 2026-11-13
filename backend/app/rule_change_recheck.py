"""Durable, scoped verification after policy changes; never rewrite replies."""
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import select, and_, or_, true, func
from .db import Memory, Draft, Review, Product, Job, StoreProfile, now, IS_SQLITE


def snapshot(rule):
    if rule is None:
        return None
    return {key: getattr(rule, key) for key in ('id', 'text', 'kind', 'scope')} | {
        'enabled': rule.enabled is not False}


def policy_version(db):
    rows = db.execute(select(Memory.id, Memory.text, Memory.kind, Memory.scope).where(
        Memory.enabled == True).order_by(Memory.id)).all()
    return hashlib.sha256(json.dumps([tuple(row) for row in rows], ensure_ascii=False).encode()).hexdigest()


def scope_conditions(state):
    """Cheap SQL narrowing; exact category/equivalence matching happens below."""
    scopes = state.get('rule_scopes')
    conditions = [~Review.status.in_(('publishing', 'published'))]
    if not scopes:
        return conditions + ([~Review.marketplace.like('%_question')] if state.get('reviews_only') else [])
    terms = []
    for rule in scopes:
        kind = rule['kind']
        audience = (Review.marketplace.like('%_question') if kind == 'QUESTION_RULE' else
                    ~Review.marketplace.like('%_question') if kind == 'REVIEW_RULE' else true())
        scope, _, target = rule['scope'].partition(':')
        if scope.casefold() == 'product':
            product = Review.product_id.in_(rule.get('product_ids') or [target])
        elif scope.casefold() == 'brand' and target.isascii():
            product = select(Product.id).where(Product.id == Review.product_id,
                func.lower(func.trim(Product.brand)) == target.strip().casefold()).exists()
        else:
            # Curated categories can match product metadata across marketplaces;
            # don't incorrectly narrow them to a marketplace's literal category.
            product = true()
        terms.append(and_(audience, product))
    return conditions + [or_(*terms)]


def affected(db, review, product, state):
    if not review or not product:
        return False
    scopes = state.get('rule_scopes')
    if not scopes:
        return not (state.get('reviews_only') and review.marketplace.endswith('_question'))
    from .owner_reply_rules import matching_owner_rules
    rows = [SimpleNamespace(**{key: item[key] for key in ('id', 'text', 'kind', 'scope')}) for item in scopes]
    return bool(matching_owner_rules(db, product, review.marketplace.endswith('_question'), rule_rows=rows))


def should_check(db, draft, review, product, state, rules=None):
    if (not draft or not review or review.is_answered or
            review.status in ('publishing', 'published') or not affected(db, review, product, state)):
        return False
    from .owner_reply_rules import matching_owner_rules
    from .owner_reply_policy import fingerprint, saved_rule_quality
    rules = matching_owner_rules(db, product, review.marketplace.endswith('_question')) if rules is None else rules
    verdict = (draft.quality or {}).get('owner_rules') or {}
    if verdict.get('fingerprint') != fingerprint(rules):
        return True
    # An unchanged, completed verdict (including a content violation) doesn't
    # improve by sending the same text through another verification scan.
    current = saved_rule_quality(draft.text, rules, verdict, review=review)
    if (verdict.get('checked') is True and not verdict.get('technical_error') and
            (current.get('passed') or verdict.get('passed') is False)):
        return False
    return verdict.get('retryable') is not False


def _queue(db, account, version, scopes, actor_id, source):
    # Serialize creation with other policy changes in this store. The unique ID
    # also protects SQLite/test databases and stores without a profile row.
    db.scalar(select(StoreProfile).where(StoreProfile.id == account).with_for_update())
    existing = db.scalar(select(Job).where(Job.kind == 'draft_recheck',
        Job.result['account_id'].as_string() == account,
        Job.result['rule_policy_version'].as_string() == version,
        Job.result['rule_change_source'].as_string() == source,
        or_(Job.status.in_(('queued', 'running', 'cancelled')), Job.cancel_requested == True))
        .order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
    if existing:
        return existing
    identity = hashlib.sha256((account + ':' + version + ':' + source).encode()).hexdigest()[:28]
    jid = 'rules-check-' + identity
    old = db.get(Job, jid)
    if old:
        # A later actual policy change can return to a previously checked policy.
        # Keep that completed audit and start a fresh pass; never reset its cursor.
        jid += '-' + uuid4().hex[:8]
    payload = {'account_id': account, 'policy_scan': True, 'check_only': True,
        'rule_change': True, 'rule_policy_version': version, 'rule_scopes': scopes,
        'reviews_only': source == 'saved-reviews', 'requested_by': actor_id,
        'rule_change_source': source, 'cursor_id': '', 'checked': 0,
        'message': 'Проверка сохранённых текстов по текущим правилам', 'requested_at': now()}
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    insert = sqlite_insert if IS_SQLITE else pg_insert
    db.execute(insert(Job).values(id=jid, kind='draft_recheck', priority=65,
        status='queued', max_attempts=1000000, result=payload).on_conflict_do_nothing(index_elements=['id']))
    return db.get(Job, jid)


def rule_changed(db, before, after, actor_id=''):
    """Call in the SAME transaction as an actual Memory mutation."""
    active = lambda value: value if value and value.get('enabled') else None
    before, after = active(before), active(after)
    if before == after:
        return []
    scopes = [item for item in (before, after) if item]
    if not scopes:
        return []
    db.flush()
    from .shared_marketplaces import equivalent_products
    for item in scopes:
        if item['scope'].casefold().startswith('product:'):
            pid = item['scope'].partition(':')[2]
            product = db.get(Product, pid)
            item['product_ids'] = [p.id for p in equivalent_products(db, product)] if product else [pid]
    version = policy_version(db)
    rebase_library_audits(db, scopes, version)
    state = {'rule_scopes': scopes}
    accounts = db.scalars(select(Review.wb_account_id).join(Draft, Draft.review_id == Review.id).where(
        Review.is_answered == False, *scope_conditions(state)).distinct()).all()
    return [_queue(db, account, version, scopes, actor_id, 'memory') for account in accounts]


def rebase_library_audits(db, scopes, version=None):
    """Invalidate only affected checked rows; preserve cursors and other results.

    Called in the policy transaction. A running inspection sees a changed input
    key and retries; stage rewinding happens at the next selection boundary.
    """
    from .db import ReviewLibraryAuditRow
    version = version or policy_version(db)
    for job in db.scalars(select(Job).where(Job.kind=='review_library_audit',
            Job.status.in_(('queued','running')), Job.cancel_requested==False).with_for_update()):
        state = dict(job.result or {})
        stages = [dict(s) for s in state.get('stages',[])]
        indices = {(s['account_id'],s['marketplace']):i for i,s in enumerate(stages)}
        # The audit includes published reference answers too; omit only the
        # generic draft-publication filter, retaining SQL audience/scope filters.
        candidates = db.execute(select(ReviewLibraryAuditRow,Review,Product)
            .join(Review,Review.id==ReviewLibraryAuditRow.review_id)
            .join(Product,Product.id==Review.product_id)
            .where(ReviewLibraryAuditRow.job_id==job.id,
                   *scope_conditions({'rule_scopes':scopes})[1:])).all()
        touched = []
        for row,review,product in candidates:
            if not affected(db,review,product,{'rule_scopes':scopes}):continue
            index = indices.get((review.wb_account_id,review.marketplace))
            if index is None:continue
            if row.status in ('verified','verified_reference','attention'):
                field = 'verified' if row.status.startswith('verified') else 'attention'
                for target in (state,stages[index]):
                    target['checked']=max(0,target.get('checked',0)-1)
                    target[field]=max(0,target.get(field,0)-1)
            if row.status=='removed':continue
            row.status='retry';row.retry_at=0;row.input_key='';row.template_id='';row.verification_progress=None
            touched.append(index)
        if touched:
            state['policy_rewind_stage']=min([state.get('stage',0),*touched,
                                             state.get('policy_rewind_stage',state.get('stage',0))])
        state.update(policy_version=version,stages=stages,retry_at=0,
            template_done=False,template_cursor='',policy_updated_at=now(),
            message='Правила обновлены: затронутые ответы поставлены на новую проверку; отправка не включалась.')
        job.result=state
        job.progress=min(99,round(state.get('checked',0)*100/max(1,state.get('total',1))))


def enqueue_draft(db, draft, review, actor_id=''):
    """One streaming catch-up per store/policy, not one job per stale draft."""
    if not review or review.marketplace.endswith('_question'):
        return None
    product = db.get(Product, review.product_id)
    if not should_check(db, draft, review, product, {'reviews_only': True}):
        return None
    return _queue(db, review.wb_account_id, policy_version(db), [], actor_id, 'saved-reviews')
