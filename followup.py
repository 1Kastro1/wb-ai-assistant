"""One-time follow-up: preserve texts, requeue content cases, never publish."""
from sqlalchemy import select
from app.db import Session, Job, Review, ReviewLibraryAuditRow
from app.library_workspace import latest_job, backfill_suggestions, category, counts
from app.rule_change_recheck import policy_version

with Session() as db:
    before_policy = policy_version(db)
    suggestions = backfill_suggestions(db)
    job = latest_job(db)
    if not job or job.cancel_requested or job.status not in ('queued', 'running'):
        raise RuntimeError('No active audit')
    state = dict(job.result)
    stages = [dict(item) for item in state['stages']]
    rows = db.execute(select(ReviewLibraryAuditRow, Review).join(Review, Review.id == ReviewLibraryAuditRow.review_id).where(
        ReviewLibraryAuditRow.job_id == job.id, ReviewLibraryAuditRow.status == 'attention')).all()
    retried = 0
    for row, review in rows:
        if category(row.issues) != 'content':
            continue
        stage = next((item for item in stages if item['account_id'] == review.wb_account_id and item['marketplace'] == review.marketplace), None)
        if stage is None:
            continue
        for target in (stage, state):
            if target.get('checked', 0) < 1 or target.get('attention', 0) < 1:
                raise RuntimeError('Unexpected audit counts')
            target['checked'] -= 1
            target['attention'] -= 1
        row.status, row.retry_at = 'retry', 0
        retried += 1
    db.flush()
    summary = counts(db, job.id)
    state.update(stages=stages, deferred=summary['retry'] + summary['blocked'], retry_at=0,
        contextual_followup='2026-11-13', message='Повторный разбор содержательных замечаний; тексты сохранены')
    job.result, job.status = state, 'queued'
    assert policy_version(db) == before_policy
    db.commit()
    print({'suggested_cases': suggestions, 'requeued_content_cases': retried, 'audit_counts': summary})


