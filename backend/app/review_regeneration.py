"""Owner-started, durable regeneration of saved, unpublished review drafts."""
import hashlib
import time
from sqlalchemy import select, func
from .db import Session, Job, Review, Draft, Publication, GenerationOutcome, StoreProfile, User, now, setting

VERSION = 'review-policy-2026-11-02-v1'


def protected_draft(db, review, draft, recovery):
    from .draft_audit import manually_edited
    job = db.get(Job, recovery['job_id'])
    return (not job or job.cancel_requested or job.status not in ('queued', 'running') or
        not review or not draft or review.wb_account_id != recovery['account_id'] or
        review.marketplace not in ('wb', 'ozon', 'yandex') or review.is_answered or
        review.status in ('publishing', 'published', 'pending_confirmation') or manually_edited(draft) or
        bool(db.scalar(select(Publication.id).where(Publication.review_id == review.id))))


def scope(account, cutoff):
    return select(Draft.id).join(Review, Review.id == Draft.review_id).where(
        Review.wb_account_id == account, Review.marketplace.in_(('wb', 'ozon', 'yandex')),
        Review.is_answered == False, ~Review.status.in_(('publishing', 'published', 'pending_confirmation')),
        Review.created_at <= cutoff,
        ~select(Publication.id).where(Publication.review_id == Review.id).correlate(Review).exists())


def enqueue_all(db, actor_id):
    actor = db.get(User, actor_id)
    if not actor or not actor.enabled or actor.role != 'owner':
        raise ValueError('Нужен действующий владелец')
    jobs = []
    for store in db.scalars(select(StoreProfile).where(StoreProfile.provider.in_(('wb', 'ozon', 'yandex'))).with_for_update()):
        # Fingerprint all applicable scopes, not merely global rules.
        from .db import Memory
        rules = [(r.id, r.text, r.scope) for r in db.scalars(select(Memory).where(Memory.enabled == True, Memory.kind != 'QUESTION_RULE').order_by(Memory.id))]
        jid = 'regenerate-' + hashlib.sha256(repr((VERSION, store.id, rules)).encode()).hexdigest()[:24]
        job = db.get(Job, jid)
        if not job:
            cutoff = now()
            total = db.scalar(select(func.count()).select_from(scope(store.id, cutoff).subquery())) or 0
            job = Job(id=jid, kind='draft_recheck', priority=45, max_attempts=1000000,
                result={'policy_regeneration': True, 'policy_version': VERSION, 'account_id': store.id,
                    'requested_by': actor_id, 'cutoff': cutoff, 'cursor_id': '', 'total': total,
                    'processed': 0, 'saved': 0, 'preserved': 0, 'errors': 0,
                    'message': 'Перегенерация сохранённых ответов по новым правилам'})
            db.add(job)
        jobs.append(job)
    db.commit()
    return jobs


async def step(job_id, session_factory=Session, limit=100):
    from .services import generate_draft
    from .generation_control import batch_context, GenerationBusy, ReviewSkipped
    started = time.monotonic()
    for _ in range(limit):
        with session_factory() as db:
            job = db.get(Job, job_id); state = dict(job.result or {})
            if job.cancel_requested or job.status in ('cancelled', 'completed'):
                if job.cancel_requested: job.status = 'cancelled'; db.commit()
                return
            if not setting(db, 'assistant_enabled', True):
                job.status = 'queued'
                job.result = {**state, 'retry_at': time.time()+60, 'message': 'Ожидаем включения ассистента. Прогресс сохранён.'}
                db.commit(); return
            did = db.scalar(scope(state['account_id'], state['cutoff']).where(Draft.id > state['cursor_id']).order_by(Draft.id).limit(1))
            if not did:
                job.status, job.progress = 'completed', 100
                job.result = {**state, 'finished_at': now(), 'message': 'Перегенерация завершена. Ручные правки и отправленные ответы сохранены.'}
                db.commit(); return
            draft = db.get(Draft, did); rid = draft.review_id
            review = db.get(Review, rid)
            recovery = {'policy_regeneration': True, 'job_id': job_id, 'account_id': state['account_id']}
            already_saved = db.get(GenerationOutcome, (job_id, rid)) is not None
            preserved = protected_draft(db, review, draft, recovery)
            error = None
            if not already_saved and not preserved:
                token = batch_context.set({'job_id': job_id, 'policy_regeneration': True})
                try:
                    await generate_draft(db, rid, recovery=recovery)
                    already_saved = True
                except GenerationBusy:
                    db.rollback()
                    job = db.get(Job, job_id)
                    job.status = 'queued'
                    job.result = {**job.result, 'retry_at': time.time()+5}
                    db.commit()
                    return  # Keep the cursor; resume after the active lease.
                except ReviewSkipped:
                    db.rollback(); preserved = True
                except Exception as failure:
                    db.rollback(); error = type(failure).__name__
                finally:
                    batch_context.reset(token)
            db.expire_all()
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            state = dict(job.result or {})
            state.update(cursor_id=did, processed=state['processed']+1,
                saved=state['saved']+int(already_saved), preserved=state['preserved']+int(preserved),
                errors=state['errors']+int(error is not None), updated_at=now(), last_served_at=now())
            if error:
                state['last_error'] = error
            state['message'] = f"Обработано: {state['processed']} из {state['total']}. Сохранено: {state['saved']}; ручных и защищённых ответов сохранено: {state['preserved']}; ошибок: {state['errors']}."
            job.result = state
            job.progress = min(99, int(100*state['processed']/max(1,state['total'])))
            if not job.cancel_requested: job.status = 'queued'
            db.commit()
        if time.monotonic()-started > 30:
            return


def runtime(db):
    rows = list(db.scalars(select(Job).where(Job.kind == 'draft_recheck', Job.result['policy_regeneration'].as_boolean() == True).order_by(Job.created_at.desc())))
    seen = set(); result = []
    for job in rows:
        state = job.result or {}; account = state.get('account_id')
        if account in seen: continue
        seen.add(account)
        store = db.get(StoreProfile, account)
        result.append({'id': job.id, 'store': store.name if store else account, 'status': job.status,
            'progress': job.progress, **{k:state.get(k) for k in ('processed','total','saved','preserved','errors','message','updated_at')}})
    return {'version': VERSION, 'stores': result}
