"""Refresh authorised unpublished drafts without calling the model for rule cases."""
import json
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import select
from .db import Draft, Review, Product
from .safety import rule_reply, safe_fallback_reply, validate_reply, mask_vin, fresh_reply_variants
from .services import trusted_product_facts, assess_reply, edit_draft, generate_draft


async def refresh_unsent_drafts(db, include_questions=True):
    query = select(Draft.id).join(Review, Review.id == Draft.review_id).where(
        Review.is_answered == False, Review.status != 'publishing')
    if not include_questions:
        query = query.where(~Review.marketplace.like('%_question'))
    ids = list(db.scalars(query.order_by(Draft.id)))
    backup = Path('/data') / ('drafts-before-owner-policy-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
    backup.write_text(json.dumps([{'id': d.id, 'review_id': d.review_id, 'revision': d.revision, 'text': d.text,
                                  'model': d.model, 'quality': d.quality, 'edits': d.edits}
                                 for did in ids if (d := db.get(Draft, did))], ensure_ascii=False), encoding='utf-8')
    backup.chmod(0o600)
    counts, failures = Counter(), []
    for index, did in enumerate(ids):
        try:
            db.rollback()
            draft = db.get(Draft, did, populate_existing=True)
            review = db.get(Review, draft.review_id, populate_existing=True)
            if review.is_answered or review.status == 'publishing':
                counts['skipped'] += 1
                continue
            if review.marketplace.endswith('_question'):
                await generate_draft(db, review.id)
                counts['questions_updated'] += 1
                continue
            product = db.get(Product, review.product_id)
            facts = trusted_product_facts(db, product)
            candidates = [rule_reply(review, product, draft.revision + offset, facts) for offset in range(3)]
            candidates = [validate_reply(mask_vin(candidate)) for candidate in candidates if candidate]
            if not candidates:
                candidates = [validate_reply(mask_vin(safe_fallback_reply(review, product, draft.revision, facts)))]
            if draft.text in candidates:
                draft.quality = assess_reply(review, product, draft.text, facts)
                review.status = 'manual_review' if review.manual or not draft.quality.get('passed') else 'draft_ready'
                db.commit()
                counts['already_correct'] += 1
                continue
            candidates = fresh_reply_variants(candidates, draft.text)
            answer = max(candidates, key=lambda value: assess_reply(review, product, value, facts)['score'])
            if answer == draft.text:
                counts['already_correct'] += 1
                continue
            edit_draft(db, draft.id, answer, draft.revision, learn_preferences=False)
            draft.model, draft.updated_by = 'owner-policy-2026-10-13', 'automation'
            counts['updated'] += 1
            if not draft.quality.get('passed'):
                counts['needs_review'] += 1
            db.commit()
        except Exception as error:
            db.rollback()
            failures.append({'draft_id': did, 'error': str(error)})
            counts['failed'] += 1
        if (index + 1) % 25 == 0:
            print('POLICY_PROGRESS', index + 1, len(ids), dict(counts), flush=True)
    print('POLICY_REFRESH_DONE', dict(counts), 'TOTAL', len(ids), 'BACKUP', str(backup), flush=True)
    if failures:
        print('POLICY_FAILURES', json.dumps(failures, ensure_ascii=False), flush=True)
    return {'total': len(ids), **dict(counts), 'failures': failures, 'backup': str(backup)}
