"""Requeue context failures and check one saved answer without publishing."""
import asyncio
from sqlalchemy import select
from app.db import Session, ReviewLibraryAuditRow, Review, Product, Draft
from app.library_workspace import latest_job

with Session() as db:
    job = latest_job(db)
    assert job and job.status in ('queued', 'running') and not job.cancel_requested
    rows = list(db.scalars(select(ReviewLibraryAuditRow).where(
        ReviewLibraryAuditRow.job_id == job.id, ReviewLibraryAuditRow.status == 'blocked')))
    selected = [row for row in rows if any('контекст' in str(issue).lower() for issue in (row.issues or []))]
    sample = None
    for row in selected:
        row.status, row.retry_at = 'retry', 0
        review = db.get(Review, row.review_id)
        product = db.get(Product, review.product_id) if review else None
        if product and (sample is None or 'hl-19' in product.name.lower()):
            sample = row.review_id
    job.result = {**job.result, 'retry_at': 0, 'context_budget_fix': '2026-11-13'}
    job.status = 'queued'
    db.commit()
    print({'requeued_context_cases': len(selected)}, flush=True)

async def check_sample():
    if sample is None:
        return
    from app.services import assess_reply, review_verified_facts
    from app.owner_reply_rules import matching_owner_rules
    from app.owner_reply_policy import enforce_owner_rules
    from app.integrations import ollama
    from app.model_context import request_tokens
    original = ollama.structured
    sizes = []
    async def measured(messages, schema, **options):
        sizes.append(request_tokens([*messages[:-1], {**messages[-1],
            'content': messages[-1]['content'] + '\n/no_think'}], schema))
        return await original(messages, schema, **options)
    ollama.structured = measured
    try:
        with Session() as db:
            review = db.get(Review, sample)
            product = db.get(Product, review.product_id)
            draft = db.scalar(select(Draft).where(Draft.review_id == sample))
            answer = review.existing_answer if review.is_answered else draft.text if draft else ''
            rules = matching_owner_rules(db, product)
            facts = review_verified_facts(db, review, product, rules)
            _, verdict = await enforce_owner_rules(answer, review, product, rules, facts,
                lambda text: assess_reply(review, product, text, facts), protected=review.risk != 'NORMAL',
                check_only=True, allow_fast_positive=True, check_seconds=120)
            print({'sample_is_requested_product': 'hl-19' in product.name.lower(),
                'checked': verdict.get('checked'), 'passed': verdict.get('passed'),
                'failure_code': verdict.get('failure_code'), 'groups': verdict.get('semantic_groups'),
                'verified_groups': verdict.get('verified_groups'), 'request_token_estimates': sizes}, flush=True)
    finally:
        ollama.structured = original

asyncio.run(check_sample())
