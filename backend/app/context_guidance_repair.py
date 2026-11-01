"""Explicit, resumable repair of known generic drafts; never publishes or calls AI."""
import hashlib
import re
import time
from sqlalchemy import select
from .db import Session, Job, Review, Draft, Product, Publication, GenerationClaim, User, now

VERSION = 'context-guidance-2026-11-02-v1'


def candidate(review, product, draft, facts):
    from .product_guidance import product_usage_reply
    from .reply_policy import analyze_customer_message, semantic_reply_issues
    topics = set(analyze_customer_message(review.text, review.rating)['complaints'])
    new_topic = bool(topics & {'SCENT_IMPRESSION', 'SURFACE_MARKS'})
    generic = bool(re.search(r'покупка не оправдала|что именно Вас не устроило|в чём проявилась проблема', draft.text, re.I))
    bad_odor = 'ODOR_COMPLAINT' in topics and bool(semantic_reply_issues(review, product, draft.text))
    return product_usage_reply(review, product, facts) if new_topic or generic or bad_odor else None


def enqueue(db, actor_id, account):
    actor = db.get(User, actor_id)
    if not actor or not actor.enabled or actor.role != 'owner':
        raise ValueError('Нужен действующий владелец')
    jid = 'guidance-' + hashlib.sha256((VERSION+account).encode()).hexdigest()[:24]
    job = db.get(Job, jid)
    if not job:
        job = Job(id=jid, kind='draft_recheck', priority=35, max_attempts=1000000,
            result={'context_guidance_repair': True, 'background': True, 'account_id': account,
                    'requested_by': actor_id, 'cursor_id': '', 'checked': 0, 'changed': 0,
                    'message': 'Уточняем ответы по аромату и применению защитного покрытия'})
        db.add(job); db.commit()
    return job


async def step(job_id, session_factory=Session, limit=100):
    from .draft_audit import manually_edited
    from .services import assess_reply, trusted_product_facts
    from .owner_reply_rules import matching_owner_rules
    from .owner_reply_policy import apply_signature, apply_local_preferences, saved_rule_quality, attach_rule_quality
    from .safety import validate_reply
    from .rule_retry import enqueue as enqueue_check
    for _ in range(limit):
        with session_factory() as db:
            job = db.get(Job, job_id); state = dict(job.result or {})
            if job.cancel_requested or job.status in ('cancelled', 'completed'):
                if job.cancel_requested: job.status = 'cancelled'; db.commit()
                return
            did = db.scalar(select(Draft.id).join(Review, Review.id == Draft.review_id).join(Product, Product.id == Review.product_id)
                .where(Review.wb_account_id == state['account_id'], Review.rating.between(1,3), Review.text != '',
                    ~Review.marketplace.like('%_question'), Review.is_answered == False, Review.status != 'publishing',
                    Product.brand.in_(('EIKOSHA','SOFT99')), Draft.id > state['cursor_id'])
                .order_by(Draft.id).limit(1))
            if not did:
                job.status, job.progress = 'completed', 100
                job.result = {**state, 'finished_at': now(), 'message': f"Проверено: {state['checked']}. Уточнено ответов: {state['changed']}. Ручные правки сохранены."}
                db.commit(); return
            rid = db.scalar(select(Draft.review_id).where(Draft.id == did))
            review = db.scalar(select(Review).where(Review.id == rid).with_for_update())
            draft = db.scalar(select(Draft).where(Draft.id == did).with_for_update())
            if db.scalar(select(GenerationClaim.review_id).where(GenerationClaim.review_id == rid, GenerationClaim.expires_at > time.time())):
                job.status='queued'; db.commit(); return
            eligible = (review and draft and not review.is_answered and review.status != 'publishing'
                and not manually_edited(draft) and review.risk == 'NORMAL' and not review.safety_critical
                and not db.scalar(select(Publication.id).where(Publication.review_id == rid)))
            if eligible and (draft.quality or {}).get('context_guidance', {}).get('version') != VERSION:
                product = db.get(Product, review.product_id); facts = trusted_product_facts(db, product)
                answer = candidate(review, product, draft, facts)
                if answer:
                    rules = matching_owner_rules(db, product)
                    answer, _ = apply_local_preferences(answer, review, rules)
                    answer = validate_reply(apply_signature(answer, rules))
                    old = draft.text
                    if answer != old:
                        verdict = saved_rule_quality(answer, rules, None, changed=True, review=review)
                        quality = attach_rule_quality({**(draft.quality or {}), **assess_reply(review, product, answer, facts)}, verdict)
                        quality['context_guidance'] = {'version': VERSION, 'at': now()}
                        draft.text, draft.revision = answer, draft.revision + 1
                        draft.edits = [*(draft.edits or []), {'old': old, 'new': answer, 'by': 'automation', 'at': now(), 'reason': VERSION}]
                        draft.quality = quality; draft.model = 'context-guidance-1.0'
                        review.status = 'manual_review'  # Low ratings still need personal review.
                        state['changed'] += 1
                        enqueue_check(db, draft, review, state['requested_by'])
            state.update(cursor_id=did, checked=state['checked']+1, updated_at=now(), last_served_at=now())
            job.result, job.status = state, 'queued'; db.commit()
