"""Resumable local repair of audited cases, without bulk model generation."""
import argparse
import hashlib
import json
import time
from sqlalchemy import select, func
from .db import Session, Job, Review, Draft, Product, User, GenerationClaim, now

VERSION = 'review-local-repair-2026-10-29-v1'


def enqueue(db, actor_id):
    actor = db.get(User, actor_id)
    if not actor or not actor.enabled or actor.role != 'owner':
        raise ValueError('Нужен действующий владелец')
    jobs = []
    accounts = db.execute(select(Review.wb_account_id, func.count(Draft.id)).join(Draft, Draft.review_id == Review.id).where(
        Review.is_answered == False, Review.status != 'publishing', ~Review.marketplace.like('%_question')
    ).group_by(Review.wb_account_id)).all()
    for account, total in accounts:
        jid = 'local-review-' + hashlib.sha256((VERSION + ':' + account).encode()).hexdigest()[:24]
        existing = db.get(Job, jid)
        if not existing:
            db.add(Job(id=jid, kind='draft_recheck', priority=75, max_attempts=1000000,
                result={'account_id':account, 'fast_repair':True, 'version':VERSION, 'total':total,
                        'requested_by':actor_id, 'message':'Проверка сохранённых ответов без ожидания модели'}))
        # Never restart a cancelled job or reset a saved cursor.
        jobs.append(jid)
    db.commit()
    return jobs


async def step(job_id, session_factory=Session, limit=500):
    from .draft_audit import manually_edited
    from .owner_reply_rules import matching_owner_rules
    from .owner_reply_policy import enforce_owner_rules, attach_rule_quality, fingerprint
    from .rule_execution import positive_template, eikosha_case_template
    from .services import assess_reply, review_verification_facts
    from .generation_control import FastPathUnavailable
    started = time.monotonic()
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job.cancel_requested:
            job.status = 'cancelled'; db.commit(); return
        state = dict(job.result or {})
        ids = list(db.scalars(select(Draft.id).join(Review, Review.id == Draft.review_id).where(
            Review.wb_account_id == state['account_id'], Review.is_answered == False,
            Review.status != 'publishing', ~Review.marketplace.like('%_question'),
            Draft.id > state.get('cursor_id', '')).order_by(Draft.id).limit(limit)))
    served = 0
    for did in ids:
        with session_factory() as db:
            job = db.get(Job, job_id)
            if job.cancel_requested:
                job.status = 'cancelled'; db.commit(); return
            state = dict(job.result or {})
            rid = db.scalar(select(Draft.review_id).where(Draft.id == did))
            review = db.scalar(select(Review).where(Review.id == rid).with_for_update())
            draft = db.scalar(select(Draft).where(Draft.id == did).with_for_update())
            if db.scalar(select(GenerationClaim.review_id).where(GenerationClaim.review_id == rid, GenerationClaim.expires_at > time.time())):
                job.status = 'queued'; db.commit(); return  # revisit without losing the cursor
            verified = changed = False
            if draft and review and not review.is_answered and review.status != 'publishing':
                product = db.get(Product, review.product_id)
                rules = matching_owner_rules(db, product)
                protected = manually_edited(draft)
                facts = []
                candidate = positive_template(review, rules, product=product)
                if not candidate and product.brand.strip().casefold() == 'eikosha':
                    facts = review_verification_facts(db, review, product, rules)
                    candidate = eikosha_case_template(review, product, rules, facts)
                # A valid current text or an employee edit may only be checked,
                # never replaced. Arbitrary text never receives a local pass.
                current = (draft.quality or {}).get('owner_rules') or {}
                if protected or ((draft.quality or {}).get('passed') and current.get('fingerprint') == fingerprint(rules)):
                    candidate = draft.text
                if candidate:
                    try:
                        answer, verdict = await enforce_owner_rules(candidate, review, product, rules, facts,
                            lambda text: assess_reply(review, product, text, facts), allow_fast_positive=True,
                            check_only=True, local_only=True)
                    except FastPathUnavailable:
                        verdict = None
                    if verdict and verdict.get('passed') and assess_reply(review, product, answer, facts)['passed']:
                        old = draft.text
                        changed = old != answer
                        if changed:
                            draft.edits = [*(draft.edits or []), {'old':old, 'new':answer, 'at':now(), 'by':'automation', 'reason':VERSION}]
                            draft.text, draft.revision = answer, draft.revision + 1
                        base = {k:v for k,v in (draft.quality or {}).items() if k not in ('text_issues', 'owner_rules')}
                        quality = attach_rule_quality({**base, **assess_reply(review, product, answer, facts)}, verdict)
                        quality['repair_audit'] = {**quality.get('repair_audit', {}), 'local_version':VERSION, 'rules_rechecked_at':now()}
                        draft.quality = quality
                        review.status = 'manual_review' if review.rating <= 3 or review.manual or review.risk != 'NORMAL' else 'draft_ready'
                        verified = True
                state['manual_preserved'] = state.get('manual_preserved', 0) + int(protected)
            state.update(cursor_id=did, checked=state.get('checked', 0)+1,
                         verified=state.get('verified', 0)+int(verified), changed=state.get('changed', 0)+int(changed),
                         deferred=state.get('deferred', 0)+int(not verified), updated_at=now(), last_served_at=now())
            state['message'] = f"Проверено: {state['checked']}; без модели подтверждено: {state['verified']}; остальные требуют проверки"
            job.result, job.status = state, 'queued'
            job.progress = min(99, round(100*state['checked']/max(1,state.get('total',0))))
            db.commit()
            served += 1
        if time.monotonic() - started > 10:
            break
    if served == len(ids) and len(ids) < limit:
        with session_factory() as db:
            job = db.get(Job, job_id)
            job.status, job.progress = ('cancelled' if job.cancel_requested else 'completed'), 100
            db.commit()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--actor', required=True)
    args = parser.parse_args()
    with Session() as db:
        print(json.dumps({'jobs':enqueue(db, args.actor)}), flush=True)
