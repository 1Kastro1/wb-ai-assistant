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
    with session_factory() as db:
        if (db.get(Job, job_id).result or {}).get('policy_regeneration'):
            from .review_regeneration import step
            return await step(job_id, session_factory)
    with session_factory() as db:
        if (db.get(Job, job_id).result or {}).get('context_guidance_repair'):
            from .context_guidance_repair import step
            return await step(job_id, session_factory)
    with session_factory() as db:
        if (db.get(Job, job_id).result or {}).get('positive_recheck'):
            from .positive_recheck import step
            return await step(job_id, session_factory)
    with session_factory() as db:
        job = db.get(Job, job_id)
        scan = bool((job.result or {}).get('policy_scan'))
    if not scan:
        return await _recheck_one(job_id, session_factory)
    # Fast no-claims checks share one short slice; a model check yields as soon
    # as it finishes. Never build a huge JSON list or restart the scan at zero.
    started = time.monotonic()
    for _ in range(20):
        await _recheck_one(job_id, session_factory)
        with session_factory() as db:
            job = db.get(Job, job_id)
            if job.status != 'queued' or job.cancel_requested or (job.result or {}).get('retry_at', 0) > time.time():
                break
        if time.monotonic() - started >= 20:
            break


def automatic_check_condition():
    from sqlalchemy import and_, or_
    # Human/catalog decisions are not outages: do not loop forever over them.
    return and_(func.coalesce(Draft.quality['owner_rules']['retryable'].as_boolean(), True),
        or_(Draft.quality_technical == True, and_(Draft.quality_pending == True,
        Review.manual == False, Review.safety_critical == False, Review.risk == 'NORMAL',
        ~Review.marketplace.like('%_question'))))


async def _recheck_one(job_id, session_factory=Session):
    from .services import assess_reply, assess_question_answer, trusted_product_facts, review_verified_facts
    from .confirmed_reply_repair import confirmed_issue, repair, repair_pending, repair_unverified
    from .owner_reply_rules import matching_owner_rules, owner_business_facts
    from .owner_reply_policy import enforce_owner_rules, attach_rule_quality
    with session_factory() as db:
        job = db.get(Job, job_id); state = dict(job.result or {})
        check_only = bool(state.get('check_only'))
        cursor = state.get('cursor', 0); ids = state.get('draft_ids', [])
        scan = bool(state.get('policy_scan'))
        if job.cancel_requested:
            job.status = 'cancelled'; db.commit(); return
        if scan:
            technical_condition = automatic_check_condition()
            if state.get('technical_scan'):
                from .review_minions import KIND, eligible_review, enabled_for, interrupted_text_check
                from .db import setting
                minion = db.get(Job, f"{KIND}:{state['account_id']}")
                if (minion and minion.status in ('queued', 'running') and not minion.cancel_requested
                    and enabled_for(db, setting(db, 'automation_schedule', {}))):
                    technical_condition = technical_condition & ~(eligible_review() & interrupted_text_check())
            query = select(Draft.id).join(Review, Review.id == Draft.review_id).where(
                Review.wb_account_id == state['account_id'], Review.is_answered == False,
                Review.status != 'publishing', Draft.id > state.get('cursor_id', ''),
                *([~Review.marketplace.like('%_question')] if state.get('reviews_only') else []))
            if state.get('technical_scan'):
                query = query.where(technical_condition)
            if state.get('rule_change'):
                from .rule_change_recheck import scope_conditions
                query = query.where(*scope_conditions(state))
            did = db.scalar(query.order_by(Draft.id).limit(1))
            if not did:
                if state.get('technical_scan'):
                    remaining = db.scalar(select(Draft.id).join(Review, Review.id == Draft.review_id).where(
                        Review.wb_account_id == state['account_id'], Review.is_answered == False,
                        Review.status != 'publishing',
                        technical_condition).limit(1))
                    if remaining:
                        from .rule_retry import retry_delay
                        cycle = state.get('cycles', 0) + 1
                        job.status = 'queued'
                        job.result = {**state, 'cursor_id': '', 'cycles': cycle,
                            'retry_at': time.time()+retry_delay(cycle), 'last_served_at': now(),
                            'message': 'Ответы сохранены. Следующий проход проверки после восстановления сервиса.'}
                        db.commit(); return
                job.status, job.progress = 'completed', 100
                job.result = {**state, 'message': f"Проверено: {state.get('checked', 0)}; подтверждено: {state.get('passed', 0)}. Остальные ответы не одобрены автоматически.", 'updated_at': now(), 'last_progress_at': now()}
                db.commit(); return
        else:
            did = ids[cursor] if cursor < len(ids) else None
        draft = db.get(Draft, did) if did else None
        rid = draft.review_id if draft else None
        if state.get('technical_scan') and did:
            job.result = {**state, 'current_draft_id': did}
            db.commit()
    attempted = False
    async def operation():
        nonlocal attempted
        with session_factory() as db:
            review, draft = db.get(Review, rid), db.get(Draft, did)
            if not review or not draft or review.is_answered or review.status == 'publishing':
                return False
            expected = (draft.revision, draft.text, review.text, review.rating, review.product_id)
            if state.get('repair_revision', state.get('target_revision', draft.revision)) != draft.revision:
                return False
            product = db.get(Product, review.product_id)
            question = review.marketplace.endswith('_question')
            from .rule_change_recheck import affected, needs_verification
            if state.get('rule_change') and not affected(db, review, product, state):
                return False
            rules = matching_owner_rules(db, product, for_questions=question)
            def current_facts():
                return (trusted_product_facts(db, product) + owner_business_facts(product, rules)
                        if question else review_verified_facts(db, review, product, rules))
            facts = current_facts()
            assess = (lambda value: assess_question_answer(review, product, value, facts, owner_rules=rules)) if question else (lambda value: assess_reply(review, product, value, facts))
            from .rule_execution import verification_key
            input_key = verification_key(draft.text, review, product, rules, facts)
            previous = (draft.quality or {}).get('owner_rules') or {}
            continuing_repair = repair_unverified(draft.quality)
            issue = confirmed_issue(review, draft.text)
            if state.get('rule_change') or (check_only and not issue and not state.get('manual_check')
                    and previous.get('input_key') == input_key):
                if not needs_verification(db, draft, review, product, rules, input_key=input_key):
                    return False
            if (check_only and not issue and not state.get('manual_check') and previous.get('input_key') == input_key
                    and previous.get('retry_at', 0) > time.time()):
                return False
            attempted = True
            protected = (manually_edited(draft) or review.manual or review.safety_critical or review.risk != 'NORMAL'
                         or bool(question and (draft.quality or {}).get('fitment')))
            repaired_quality = None
            learned = None
            from .simple_question_templates import METHOD, STALE, stale_question_template
            stale_template = question and stale_question_template(db, review, draft, product, rules)
            if check_only and not issue and not continuing_repair and not question and not protected:
                from .learned_reply_templates import load_template
                learned = load_template(db, review, product, rules, facts, assess, exact_answer=draft.text)
            if stale_template:
                from .owner_reply_policy import fingerprint
                answer = draft.text
                verdict = {'matched': len(rules), 'fingerprint': fingerprint(rules), 'checked': True,
                           'passed': False, 'verification_method': METHOD, 'issues': [STALE], 'violations': [STALE]}
            elif issue:
                answer, repaired_quality = await repair(
                    review, product, draft.text, draft.quality or {}, rules, facts, assess, protected=protected)
                verdict = repaired_quality.get('owner_rules') or {}
            elif learned:
                answer, verdict = learned
            else:
                from .generation_budget import OWNER_RULE_CHECK_SECONDS, MANUAL_RULE_CHECK_SECONDS, AUTO_RULE_CHECK_SECONDS
                answer, verdict = await enforce_owner_rules(draft.text, review, product, rules, facts,
                    assess, protected=review.risk != 'NORMAL' or bool(question and (draft.quality or {}).get('fitment')),
                    check_seconds=MANUAL_RULE_CHECK_SECONDS if state.get('manual_check') else
                        AUTO_RULE_CHECK_SECONDS if check_only else OWNER_RULE_CHECK_SECONDS,
                    check_only=check_only or continuing_repair, previous_check=previous,
                    group_limit=1 if check_only or continuing_repair else None,
                    allow_fast_positive=check_only)
            db.rollback()
            review = db.scalar(select(Review).where(Review.id == rid).with_for_update())
            draft = db.scalar(select(Draft).where(Draft.id == did).with_for_update())
            if (not draft or not review or db.get(Job, job_id).cancel_requested or review.is_answered
                    or review.status == 'publishing' or expected != (draft.revision, draft.text, review.text, review.rating, review.product_id)):
                return False
            # Never apply a model rewrite to an employee's/manual answer, nor
            # allow a model repair that fails the independent semantic checks.
            if (rules != matching_owner_rules(db, product, for_questions=question)
                    or facts != current_facts()):
                return False
            checked = assess(answer)
            if answer != draft.text and (protected or not checked['passed'] or
                    ((check_only or continuing_repair) and not repaired_quality)):
                verdict = {**verdict, 'passed': False, 'checked': False,
                           'issues': ['Предложенный вариант требует ручной проверки; исходный текст сохранён']}
                answer = draft.text
            if answer != draft.text:
                draft.edits = [*(draft.edits or []), {'old': draft.text, 'new': answer, 'at': now(), 'by': 'automation',
                    'reason': 'confirmed_reply_repair' if repaired_quality else VERSION + '-rules'}]
                draft.text, draft.revision = answer, draft.revision + 1
            fresh_quality = {**(draft.quality or {}), **assess(answer)}
            fresh_quality.pop('text_issues', None)
            fresh_quality.pop('owner_rules', None)
            # One shared result/backoff for this exact saved text, policy and
            # context, regardless of which automatic scan encounters it next.
            from .rule_retry import technical_check, retry_delay
            from .rule_retry import checkpoint_verdict
            final_key=verification_key(answer,review,product,rules,facts)
            if repaired_quality is not None:
                quality = {**(draft.quality or {}), **repaired_quality}
                if answer != expected[1]:
                    quality.pop('reply_supervisor', None)
                    quality['owner_rules'] = checkpoint_verdict(
                        quality['owner_rules'], {}, final_key, now())
                else:
                    # A failed repair must not keep retrying the original
                    # defect as though it were a transient checker outage.
                    quality.pop('owner_rules', None)
            else:
                verdict = checkpoint_verdict(verdict, previous, final_key, now(),
                    manual=bool(state.get('manual_check') and not state.get('check_retries')))
                quality = attach_rule_quality(fresh_quality, verdict)
                if continuing_repair:
                    audit = draft.quality['confirmed_repair']
                    if quality['passed'] and quality['owner_rules'].get('checked_at') and verdict.get('checked') is True:
                        quality['confirmed_repair'] = {**audit, 'status': 'rechecked'}
                    elif not technical_check(quality):
                        quality['confirmed_repair'] = {**audit, 'status': 'blocked'}
            quality['repair_audit'] = {**(draft.quality or {}).get('repair_audit', {}), 'rules_rechecked_at': now()}
            draft.quality = quality
            review.status = 'manual_review' if review.manual or not quality['passed'] else 'draft_ready'
            if not question and quality['passed']:
                from .learned_reply_templates import remember_template
                remember_template(db, review, product, rules, facts, draft)
            db.commit()
            if quality['passed']:
                from .scheduled_publication import handoff_verified_draft
                handoff_verified_draft(db, review, draft)
            from .db import setting
            if not question and not quality['passed'] and not verdict.get('technical_error') and not technical_check(quality) and setting(db,'auto_case_rules_enabled',False):
                from types import SimpleNamespace
                from .library_workspace import suggest_case_rule
                suggest_case_rule(db,SimpleNamespace(status='attention',issues=quality.get('issues',[])),review)
                db.commit()
            return quality['passed']
    passed = await exclusive_generation(rid, operation, session_factory) if rid else False
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job.cancel_requested:
            job.status = 'cancelled'; db.commit(); return
        state = dict(job.result or {})
        from .rule_retry import technical_check
        draft = db.get(Draft, did) if did else None
        review = db.get(Review, rid) if rid else None
        pending_repair = bool(draft and repair_pending(draft.quality))
        retry = bool((check_only or pending_repair) and draft and review and not review.is_answered and review.status != 'publishing'
                     and state.get('repair_revision', state.get('target_revision', draft.revision)) == draft.revision
                     and (not state.get('rule_change') or attempted)
                     and technical_check(draft.quality))
        if pending_repair:
            if scan:
                from .rule_retry import enqueue
                enqueue(db, draft, review, state.get('requested_by', ''), confirmed_repair=True)
                retry = False
            else:
                state.update(repair_revision=draft.revision, check_only=True)
        continuation=bool(retry and (draft.quality or {}).get('owner_rules',{}).get('continuation'))
        if continuation:
            state.update(check_retries=0,retry_at=0,last_served_at=now(),updated_at=now(),
                message='Проверка продолжается с сохранённого этапа')
            job.result,job.status=state,'queued'
            db.commit()
            return
        transferred = False
        if retry and not pending_repair:
            from .review_minions import handoff
            transferred = handoff(db, draft, review) is not None
            retry = not transferred
        if retry and scan:
            if not state.get('technical_scan'):
                from .rule_retry import enqueue
                enqueue(db, draft, review, state.get('requested_by',''))
            state['deferred'] = state.get('deferred',0) + 1
        elif retry:
            from .rule_retry import retry_delay
            continuation = bool((draft.quality or {}).get('owner_rules', {}).get('continuation'))
            count = 0 if continuation else state.get('check_retries', 0) + 1
            state.update(check_retries=count, retry_at=time.time() + (0 if continuation else retry_delay(count)), last_served_at=now(),
                         updated_at=now(), message='Проверка продолжается с сохранённого этапа' if continuation else 'Сервис проверки не ответил; сохранённый текст ожидает повтора')
            job.result, job.status = state, 'queued'
            db.commit()
            return
        state.update(cursor=cursor + 1, checked=cursor + 1, passed=state.get('passed', 0) + int(passed), updated_at=now())
        if scan:
            state.update(cursor_id=did, check_retries=0, retry_at=0)
        total = max(cursor + 1, state.get('total', 0)) if scan else len(ids)
        state['message'] = ('Черновик передан миньону' if transferred else
            f'Проверка правил: {cursor + 1} из {total}')
        job.result = state
        job.progress = min(99 if scan else 100, round(100 * (cursor + 1) / max(1, total)))
        job.status = 'queued' if scan else 'completed' if cursor + 1 >= len(ids) else 'queued'
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

