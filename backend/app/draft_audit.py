"""Resumable repair of unpublished drafts; manual edits are never overwritten."""
import asyncio
import time
from uuid import uuid4
from sqlalchemy import select, func
from .db import Session, Job, Draft, Review, Product, GenerationClaim, now
from .generation_control import exclusive_generation, FastPathUnavailable

VERSION = 'review-repair-2026-10-25-v3'


def manually_edited(draft):
    return bool(any(edit.get('by') not in (None, '', 'automation') for edit in draft.edits or [])
                or (draft.updated_by and draft.text != draft.original))


async def repair_step(job_id, session_factory=Session, limit=25):
    from .services import assess_reply, trusted_product_facts
    from .owner_reply_rules import matching_owner_rules
    from .owner_reply_policy import apply_local_preferences, apply_signature, saved_rule_quality, attach_rule_quality, enforce_owner_rules
    from .rule_execution import positive_template
    from .safety import rule_reply, safe_fallback_reply, validate_reply, mask_vin
    with session_factory() as db:
        job = db.get(Job, job_id)
        state = dict(job.result or {})
        account = state['account_id']
        if not state.get('total'):
            state['total'] = db.scalar(select(func.count()).select_from(Draft).join(Review, Review.id == Draft.review_id).where(
                Review.wb_account_id == account, Review.marketplace == 'yandex',
                Review.is_answered == False, Review.status != 'publishing')) or 0
            job.result = state
            db.commit()
        ids = list(db.scalars(select(Draft.id).join(Review, Review.id == Draft.review_id).where(
            Review.wb_account_id == account, Review.marketplace == 'yandex',
            Review.is_answered == False, Review.status != 'publishing', Draft.id > state.get('cursor', ''),
        ).order_by(Draft.id).limit(limit)))
    for did in ids:
        with session_factory() as db:
            job = db.get(Job, job_id)
            if job.cancel_requested:
                job.status = 'cancelled'; db.commit(); return
            # Same review -> draft lock order as publication/editing.
            rid = db.scalar(select(Draft.review_id).where(Draft.id == did))
            review = db.scalar(select(Review).where(Review.id == rid).with_for_update())
            draft = db.scalar(select(Draft).where(Draft.id == did).with_for_update())
            if not review or not draft or review.is_answered or review.status == 'publishing':
                continue
            if db.scalar(select(GenerationClaim.review_id).where(GenerationClaim.review_id == rid, GenerationClaim.expires_at > time.time())):
                # Revisit the current cursor after the active generation finishes.
                job.status = 'queued'; db.commit(); return
            state = dict(job.result or {})
            if (draft.quality or {}).get('repair_audit', {}).get('version') != VERSION:
                product = db.get(Product, review.product_id)
                facts = trusted_product_facts(db, product)
                rules = matching_owner_rules(db, product)
                old = draft.text
                protected = manually_edited(draft)
                local = None if protected else positive_template(review, rules, product=product)
                candidate = old
                if not protected:
                    candidate = local or rule_reply(review, product, facts=facts) or safe_fallback_reply(review, product, facts=facts)
                    candidate, _ = apply_local_preferences(candidate, review, rules, style_only=review.risk != 'NORMAL')
                    candidate = apply_signature(candidate, rules)
                    try:
                        candidate = validate_reply(mask_vin(candidate))
                    except ValueError:
                        candidate = old
                changed = candidate != old
                rule_quality = saved_rule_quality(candidate, rules, (draft.quality or {}).get('owner_rules'), changed=changed, review=review)
                if local:
                    try:
                        candidate, rule_quality = await enforce_owner_rules(candidate, review, product, rules, facts,
                            lambda value: assess_reply(review, product, value, facts), allow_fast_positive=True, local_only=True)
                    except FastPathUnavailable:
                        pass
                quality = attach_rule_quality({**(draft.quality or {}), **assess_reply(review, product, candidate, facts)}, rule_quality)
                if changed:
                    draft.edits = [*(draft.edits or []), {'old': old, 'new': candidate, 'at': now(), 'by': 'automation', 'reason': VERSION}]
                    draft.text, draft.revision = candidate, draft.revision + 1
                    if local:
                        draft.model = 'positive-local-2.1'
                quality['repair_audit'] = {'version': VERSION, 'at': now(), 'manual_preserved': protected, 'changed': changed}
                draft.quality = quality
                review.status = 'manual_review' if review.manual or not quality['passed'] else 'draft_ready'
                state['checked'] = state.get('checked', 0) + 1
                state['changed'] = state.get('changed', 0) + int(changed)
                state['manual_preserved'] = state.get('manual_preserved', 0) + int(protected)
                if not quality['passed']:
                    pending = state.get('recheck_ids', [])
                    if did not in pending:
                        state['recheck_ids'] = [*pending, did]
            state.update(cursor=did, updated_at=now(), last_served_at=now(), message=f"Перепроверено ответов: {state.get('checked', 0)}")
            job.result = state
            job.progress = min(99, round(100 * state.get('checked', 0) / max(1, state.get('total', 0))))
            db.commit()
        await asyncio.sleep(0)
    with session_factory() as db:
        job = db.get(Job, job_id)
        state = dict(job.result or {})
        if len(ids) == limit:
            job.status = 'queued'
        else:
            job.status, job.progress = 'completed', 100
            pending = state.get('recheck_ids', [])
            if pending:
                check = Job(id=str(uuid4()), kind='draft_recheck', priority=60, max_attempts=1000,
                            result={'account_id': account, 'draft_ids': pending, 'cursor': 0,
                                    'source_audit': job_id, 'message': 'Проверка правил исправленных ответов'})
                db.add(check)
                state['recheck_job'] = check.id
            state['message'] = f"Перепроверено: {state.get('checked', 0)}. Исправлено: {state.get('changed', 0)}. Ручных правок сохранено: {state.get('manual_preserved', 0)}."
        job.result = state
        db.commit()


async def recheck_step(job_id, session_factory=Session):
    from .services import assess_reply, assess_question_answer, trusted_product_facts
    from .owner_reply_rules import matching_owner_rules
    from .owner_reply_policy import enforce_owner_rules, attach_rule_quality
    with session_factory() as db:
        job = db.get(Job, job_id); state = dict(job.result or {})
        cursor = state.get('cursor', 0); ids = state.get('draft_ids', [])
        did = ids[cursor] if cursor < len(ids) else None
        draft = db.get(Draft, did) if did else None
        rid = draft.review_id if draft else None
    async def operation():
        with session_factory() as db:
            review, draft = db.get(Review, rid), db.get(Draft, did)
            if not review or not draft or review.is_answered or review.status == 'publishing':
                return False
            expected = (draft.revision, draft.text, review.text, review.rating, review.product_id)
            product = db.get(Product, review.product_id)
            question = review.marketplace.endswith('_question')
            facts = trusted_product_facts(db, product)
            rules = matching_owner_rules(db, product, for_questions=question)
            assess = (lambda value: assess_question_answer(review, product, value, facts, owner_rules=rules)) if question else (lambda value: assess_reply(review, product, value, facts))
            protected = manually_edited(draft)
            answer, verdict = await enforce_owner_rules(draft.text, review, product, rules, facts,
                assess, protected=review.risk != 'NORMAL' or bool(question and (draft.quality or {}).get('fitment')), check_seconds=600)
            db.rollback()
            review = db.scalar(select(Review).where(Review.id == rid).with_for_update())
            draft = db.scalar(select(Draft).where(Draft.id == did).with_for_update())
            if not draft or review.is_answered or review.status == 'publishing' or expected != (draft.revision, draft.text, review.text, review.rating, review.product_id):
                return False
            # Never apply a model rewrite to an employee's/manual answer, nor
            # allow a model repair that fails the independent semantic checks.
            checked = assess(answer)
            if answer != draft.text and (protected or not checked['passed']):
                verdict = {**verdict, 'passed': False, 'checked': False,
                           'issues': ['Предложенный вариант требует ручной проверки; исходный текст сохранён']}
                answer = draft.text
            if answer != draft.text:
                draft.edits = [*(draft.edits or []), {'old': draft.text, 'new': answer, 'at': now(), 'by': 'automation', 'reason': VERSION + '-rules'}]
                draft.text, draft.revision = answer, draft.revision + 1
            quality = attach_rule_quality({**(draft.quality or {}), **assess(answer)}, verdict)
            quality['repair_audit'] = {**(draft.quality or {}).get('repair_audit', {}), 'rules_rechecked_at': now()}
            draft.quality = quality
            review.status = 'manual_review' if review.manual or not quality['passed'] else 'draft_ready'
            db.commit()
            return quality['passed']
    passed = await exclusive_generation(rid, operation, session_factory) if rid else False
    with session_factory() as db:
        job = db.get(Job, job_id)
        state = dict(job.result or {})
        state.update(cursor=cursor + 1, checked=cursor + 1, passed=state.get('passed', 0) + int(passed), updated_at=now())
        state['message'] = f'Проверка правил: {cursor + 1} из {len(ids)}'
        job.result, job.progress = state, round(100 * (cursor + 1) / max(1, len(ids)))
        job.status = 'completed' if cursor + 1 >= len(ids) else 'queued'
        db.commit()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--store', required=True)
    args = parser.parse_args()
    with Session() as db:
        existing = db.scalar(select(Job).where(Job.kind == 'draft_audit', Job.result['account_id'].as_string() == args.store,
                                             Job.result['version'].as_string() == VERSION))
        if not existing:
            existing = Job(id=str(uuid4()), kind='draft_audit', priority=5, max_attempts=1000,
                           result={'account_id': args.store, 'version': VERSION, 'message': 'Перепроверка сохранённых ответов'})
            db.add(existing); db.commit()
        print(existing.id, existing.status)
