from datetime import datetime, timezone
from sqlalchemy import event
from app.db import Session, Draft, Review
from app.services import quality_report, QUALITY_EVALUATOR_VERSION


def test_summary_counts_saved_checks_without_loading_drafts_or_writing(client, seeded):
    with Session() as db:
        for rid, score, edits, updated in [
            ('r1', 96, [{'by': 'automation'}], ''),
            ('r2', 80, [{'by': 'employee'}], ''),
            ('r3', 90, [], 'employee'),
            ('r4', None, [], ''),
        ]:
            db.get(Review, rid).created_at = datetime.now(timezone.utc).isoformat()
            quality = {'score': score, 'passed': score is not None and score >= 90,
                       'evaluator_version': QUALITY_EVALUATOR_VERSION}
            db.add(Draft(id='summary-' + rid, review_id=rid, text='Changed', original='Original',
                         quality=quality, edits=edits, updated_by=updated, model='fallback' if rid == 'r4' else 'rules'))
        db.commit()
    statements = []
    with Session() as db:
        engine = db.get_bind()
        def capture(conn, cursor, statement, *args):
            statements.append(statement)
        event.listen(engine, 'before_cursor_execute', capture)
        try:
            result = quality_report(db)
            assert not db.identity_map  # No Draft/Review objects or bodies loaded.
            assert not db.dirty
        finally:
            event.remove(engine, 'before_cursor_execute', capture)
    assert (result['drafts'], result['scored'], result['passed']) == (4, 3, 2)
    assert (result['edited'], result['automated_changes'], result['fallbacks']) == (2, 1, 1)
    assert result['average_score'] == 88.7
    assert not any(s.lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for s in statements)
