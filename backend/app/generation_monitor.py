"""Bounded, store-scoped timing summaries without buyer text or model prompts."""
from collections import Counter
from statistics import median
from sqlalchemy import select, func, case
from datetime import datetime, timedelta, timezone
from .db import Draft, Review, Job, GenerationOutcome


def recent_generation(db, store_id, minutes=30):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    # Old outcomes deliberately have no timestamp: no invented historical rate.
    saved, verified = db.execute(select(func.count(func.distinct(GenerationOutcome.review_id)), func.count(func.distinct(case((Draft.quality_passed == True, GenerationOutcome.review_id), else_=None))))
        .select_from(GenerationOutcome).join(Review, Review.id == GenerationOutcome.review_id)
        .outerjoin(Draft, Draft.review_id == Review.id)
        .where(GenerationOutcome.created_at >= cutoff, Review.wb_account_id == store_id,
               GenerationOutcome.category == 'generated')).one()
    return {'window_minutes': minutes, 'saved': saved or 0, 'verified': verified or 0,
            'saved_per_minute': round((saved or 0) / minutes, 2)}

GENERATION_KINDS = ('drafts', 'question_drafts', 'single_draft', 'single_question_draft')


def timing_summary(qualities, jobs):
    timings = [value.get('timing', {}) for value in qualities if isinstance(value, dict) and value.get('timing')]
    routes = Counter(value.get('route', 'rules') for value in timings)
    reasons = Counter(value['fallback_reason'] for value in timings if value.get('fallback_reason'))
    job_timings = [(item.get('result') or {}).get('timing', {}) for item in jobs]
    queues = [value['queue_seconds'] for value in job_timings if isinstance(value.get('queue_seconds'), (int, float))]
    totals = [value['total_seconds'] for value in timings if isinstance(value.get('total_seconds'), (int, float))]
    checks = [(value.get('owner_rules') or {}).get('verification_seconds') for value in qualities if isinstance(value, dict)]
    checks = [value for value in checks if isinstance(value, (int,float))]
    model_times = [value['model_seconds'] for value in timings
                   if isinstance(value.get('model_seconds'), (int,float)) and value['model_seconds'] > 0]
    return {'measured_drafts': len(timings), 'sample_jobs': len(jobs),
            'median_reply_seconds': round(median(totals), 2) if totals else None,
            'median_queue_seconds': round(median(queues), 2) if queues else None,
            'median_verification_seconds': round(median(checks), 2) if checks else None,
            'verification_samples': len(checks),
            'median_model_seconds': round(median(model_times), 2) if model_times else None,
            'model_samples': len(model_times),
            'failed_jobs': sum(item.get('status') == 'failed' for item in jobs),
            'routes': dict(routes), 'fallback_reasons': dict(reasons)}


def generation_metrics(db, store_id):
    qualities = list(db.scalars(select(Draft.quality).join(Review, Review.id == Draft.review_id)
                               .where(Review.wb_account_id == store_id)
                               .order_by(Review.created_at.desc()).limit(500)))
    jobs = list(db.scalars(select(Job).where(Job.kind.in_(GENERATION_KINDS),
                                           Job.result['account_id'].as_string() == store_id)
                          .order_by(Job.created_at.desc()).limit(200)))
    return timing_summary(qualities, [{'status': job.status, 'result': job.result} for job in jobs])
