"""Bounded, store-scoped timing summaries without buyer text or model prompts."""
from collections import Counter
from statistics import median
from sqlalchemy import select
from .db import Draft, Review, Job

GENERATION_KINDS = ('drafts', 'question_drafts', 'single_draft', 'single_question_draft')


def timing_summary(qualities, jobs):
    timings = [value.get('timing', {}) for value in qualities if isinstance(value, dict) and value.get('timing')]
    routes = Counter(value.get('route', 'rules') for value in timings)
    reasons = Counter(value['fallback_reason'] for value in timings if value.get('fallback_reason'))
    job_timings = [(item.get('result') or {}).get('timing', {}) for item in jobs]
    queues = [value['queue_seconds'] for value in job_timings if isinstance(value.get('queue_seconds'), (int, float))]
    totals = [value['total_seconds'] for value in timings if isinstance(value.get('total_seconds'), (int, float))]
    return {'measured_drafts': len(timings), 'sample_jobs': len(jobs),
            'median_reply_seconds': round(median(totals), 2) if totals else None,
            'median_queue_seconds': round(median(queues), 2) if queues else None,
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
