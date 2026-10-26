"""Read saved quality counters without loading or modifying draft bodies."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, case, and_, or_, text
from .db import Draft, Review


def quality_summary(db, days, account_id, evaluator_version):
    filters = [~Review.marketplace.like('%_question')]
    if days:
        filters.append(Review.created_at >= (datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
    if account_id:
        filters.append(Review.wb_account_id == account_id)
    current = Draft.quality['evaluator_version'].as_string() == evaluator_version
    score = Draft.quality['score'].as_integer()
    if db.bind.dialect.name == 'postgresql':
        elements = "json_array_elements(CASE WHEN json_typeof(generated_answers.edits)='array' THEN generated_answers.edits ELSE '[]'::json END) AS e(value)"
        actor = "e.value->>'by'"
    else:
        elements = "json_each(CASE WHEN json_type(generated_answers.edits)='array' THEN generated_answers.edits ELSE '[]' END) AS e"
        actor = "CASE WHEN e.type='object' THEN json_extract(e.value,'$.by') END"
    automated = text(f"EXISTS (SELECT 1 FROM {elements} WHERE {actor} = 'automation')")
    human_edit = text(f"EXISTS (SELECT 1 FROM {elements} WHERE coalesce({actor}, '') NOT IN ('', 'automation'))")
    edited = or_(and_(Draft.updated_by != '', Draft.text != Draft.original), human_edit)
    count_if = lambda condition: func.coalesce(func.sum(case((condition, 1), else_=0)), 0)
    total, scored, passed, average, edited_count, automated_count, fallbacks, stale = db.execute(
        select(func.count(), count_if(and_(current, score.is_not(None))),
               count_if(and_(current, Draft.quality['passed'].as_boolean() == True)),
               func.avg(case((current, score), else_=None)), count_if(edited), count_if(automated),
               count_if(Draft.model.like('%fallback%')),
               count_if(or_(Draft.quality['evaluator_version'].as_string().is_(None), ~current)))
        .select_from(Draft).join(Review, Review.id == Draft.review_id).where(*filters)
    ).one()
    return {'scope': 'review_created_at', 'store_id': account_id, 'period_days': days,
            'drafts': total, 'scored': scored, 'passed': passed,
            'average_score': round(float(average), 1) if average is not None else 0,
            'edited': edited_count, 'edit_rate': round(edited_count / total * 100, 1) if total else 0,
            'automated_changes': automated_count, 'fallbacks': fallbacks,
            'needs_recheck': stale, 'basis': 'saved_checks'}
