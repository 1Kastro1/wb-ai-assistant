"""Bounded local verification of unpublished positive review drafts."""
import argparse
import asyncio
import hashlib
import json
import time
from sqlalchemy import select, func
from .db import Session, Job, Review, Draft, Product, User, GenerationClaim, Publication, now

VERSION = 'positive-recheck-2026-11-01-concise-v2'


def scope(account, since=''):
    return (Review.wb_account_id == account, Review.rating.in_((4, 5)),
        Review.is_answered == False, Review.status != 'publishing',
        ~Review.marketplace.like('%_question'), Review.created_at >= since,
        ~select(Publication.id).where(Publication.review_id == Review.id).correlate(Review).exists())


def enqueue(db, actor_id, account, since='', *, refresh_templates=False):
    actor = db.get(User, actor_id)
    if not actor or not actor.enabled or actor.role != 'owner':
        raise ValueError('Нужен действующий владелец')
    jid = 'positive-recheck-' + hashlib.sha256((VERSION + ':' + account + ':' + since + (':refresh' if refresh_templates else '')).encode()).hexdigest()[:24]
    existing = db.get(Job, jid)
    if existing:
        return existing  # Never reset progress or restart cancelled work.
    total = db.scalar(select(func.count()).select_from(Draft).join(Review, Review.id == Draft.review_id).where(*scope(account, since))) or 0
    job = Job(id=jid, kind='draft_recheck', priority=35, max_attempts=1000000,
        result={'account_id': account, 'positive_recheck': True, 'version': VERSION, 'total': total, 'since': since,
                'refresh_templates': refresh_templates,
                'background': bool(refresh_templates and not since),
                'requested_by': actor_id, 'message': 'Быстрая перепроверка положительных отзывов без модели'})
    db.add(job); db.commit()
    return job


async def step(job_id, session_factory=Session, limit=500):
    from .draft_audit import manually_edited
    from .owner_reply_rules import matching_owner_rules
    from .owner_reply_policy import enforce_owner_rules, attach_rule_quality
    from .rule_execution import positive_template, legacy_positive_template
    from .services import assess_reply, review_verified_facts
    from .generation_control import FastPathUnavailable
    started = time.monotonic()
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job.cancel_requested:
            job.status = 'cancelled'; db.commit(); return
        state = dict(job.result or {})
        ids = list(db.scalars(select(Draft.id).join(Review, Review.id == Draft.review_id).where(
            *scope(state['account_id'], state.get('since', '')), Draft.id > state.get('cursor_id', '')).order_by(Draft.id).limit(limit)))
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
                job.status = 'queued'; db.commit(); return
            verified = changed = False
            protected = bool(draft and manually_edited(draft))
            if (review and draft and review.rating in (4, 5) and not review.is_answered
                and review.status != 'publishing' and not review.manual and review.risk == 'NORMAL'
                and not review.safety_critical and not db.scalar(select(Publication.id).where(Publication.review_id == rid))):
                product = db.get(Product, review.product_id)
                rules = matching_owner_rules(db, product)
                facts = review_verified_facts(db, review, product, rules)
                template = positive_template(review, rules, product=product)
                # Preserve current valid answers and all employee edits. Only
                # exact audited templates may pass locally; arbitrary prose defers.
                refresh = bool(state.get('refresh_templates') and not protected and template
                               and legacy_positive_template(draft.text, review, rules))
                candidate = template if refresh else draft.text if protected or (draft.quality or {}).get('passed') else template
                if candidate:
                    try:
                        answer, verdict = await enforce_owner_rules(candidate, review, product, rules, facts,
                            lambda text: assess_reply(review, product, text, facts), allow_fast_positive=True,
                            check_only=True, local_only=True)
                    except FastPathUnavailable:
                        verdict = None
                    if verdict and verdict.get('passed'):
                        base = assess_reply(review, product, answer, facts)
                        if base['passed']:
                            old = draft.text
                            changed = old != answer
                            if changed:
                                draft.edits = [*(draft.edits or []), {'old': old, 'new': answer, 'at': now(), 'by': 'automation', 'reason': VERSION}]
                                draft.text, draft.revision = answer, draft.revision + 1
                            quality = {k: v for k, v in (draft.quality or {}).items() if k not in ('text_issues', 'owner_rules')}
                            draft.quality = attach_rule_quality({**quality, **base}, verdict)
                            review.status = 'draft_ready'
                            from .learned_reply_templates import remember_template
                            remember_template(db, review, product, rules, facts, draft)
                            verified = True
            state.update(cursor_id=did, checked=state.get('checked', 0) + 1,
                verified=state.get('verified', 0) + int(verified), changed=state.get('changed', 0) + int(changed),
                manual_preserved=state.get('manual_preserved', 0) + int(protected),
                deferred=state.get('deferred', 0) + int(not verified), updated_at=now(), last_served_at=now())
            state['message'] = f"Проверено: {state['checked']}; подтверждено без модели: {state['verified']}; остальные требуют проверки"
            job.result, job.status = state, 'queued'
            job.progress = min(99, round(100 * state['checked'] / max(1, state.get('total', 0))))
            db.commit()
            served += 1
        await asyncio.sleep(0)
        if time.monotonic() - started >= 10:
            break
    if served == len(ids) and len(ids) < limit:
        with session_factory() as db:
            job = db.get(Job, job_id)
            job.status, job.progress = ('cancelled' if job.cancel_requested else 'completed'), 100
            db.commit()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--actor', required=True); parser.add_argument('--store', required=True); parser.add_argument('--since', default='')
    parser.add_argument('--refresh-templates', action='store_true')
    args = parser.parse_args()
    with Session() as db:
        job = enqueue(db, args.actor, args.store, args.since, refresh_templates=args.refresh_templates)
        print(json.dumps({'id': job.id, 'status': job.status, 'total': job.result['total']}), flush=True)

