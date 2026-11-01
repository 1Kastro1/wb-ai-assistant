from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select, update
from app.db import Session, Product, Review, Draft, GenerationOutcome, Job, set_setting
from app.generation_monitor import recent_generation
from app.services import question_intent


@pytest.mark.parametrize('text,intent', [
    ('С какой стороны открывать ароматизатор? Запаха нет', 'применение'),
    ('Как активировать ароматизатор, две защитные крышки?', 'применение'),
    ('Когда будет в наличии этот запах?', 'наличие'),
    ('Есть ли другой аромат?', 'наличие'),
    ('Какой размер крышки?', 'размер'),
    ('Крышка входит в комплект?', 'комплектность'),
    ('Denso подойдёт на Toyota?', 'совместимость'),
])
def test_question_intent_explains_application(text, intent):
    assert question_intent(text) == intent


def test_quality_projections_track_sql_updates_without_touching_saved_text(client):
    with Session() as db:
        db.add(Product(id='p', name='Test')); db.flush()
        db.add(Review(id='r', wb_review_id='r', product_id='p', text='', rating=5)); db.flush()
        db.add(Draft(id='d', review_id='r', text='Employee text', original='Original', revision=8,
            quality={'passed': False, 'owner_rules': {'technical_error': True, 'checked': False}})); db.commit()
        draft = db.get(Draft, 'd')
        assert (draft.quality_passed, draft.quality_technical, draft.quality_pending, draft.quality_attention) == (False, True, True, False)
        db.execute(update(Draft).where(Draft.id == 'd').values(quality={'passed': True, 'owner_rules': {'passed': True, 'checked': True}})); db.commit(); db.expire_all()
        draft = db.get(Draft, 'd')
        assert (draft.quality_passed, draft.quality_technical, draft.quality_pending, draft.quality_attention) == (True, False, False, False)
        assert (draft.text, draft.original, draft.revision) == ('Employee text', 'Original', 8)


def test_queue_separates_outage_and_low_rating_without_losing_saved_reply(client):
    with Session() as db:
        db.add(Product(id='p', name='Test')); db.flush()
        db.add(Review(id='r', wb_review_id='r', product_id='p', text='', rating=2)); db.flush()
        db.add(Draft(id='d', review_id='r', text='Saved', original='Saved', quality={
            'passed': False, 'owner_rules': {'passed': False, 'checked': False, 'technical_error': True}})); db.commit()
    result = client.get('/work-queue?view=technical').json()
    assert result['total'] == 1
    assert result['items'][0]['verification_state'] == 'technical'
    assert result['items'][0]['manual'] is True
    assert result['items'][0]['draft_id'] == 'd'


def test_scoped_recheck_cannot_expand_member_store_access(client):
    from fastapi import HTTPException, BackgroundTasks
    from app.main import retry_draft_rules
    with Session() as db:
        db.add(Product(id='p', name='Test')); db.flush()
        db.add(Review(id='r', wb_review_id='r', wb_account_id='foreign', product_id='p', text='', rating=5)); db.flush()
        db.add(Draft(id='d', review_id='r', text='Saved', original='Saved')); db.commit()
        with pytest.raises(HTTPException) as error:
            retry_draft_rules('d', BackgroundTasks(), {'role': 'member', 'user_id': 'owner'}, db, store_id='foreign')
        assert error.value.status_code == 404


def test_catalog_conflicts_are_filterable_and_never_rewrite_identity(client):
    with Session() as db:
        db.add_all([Product(id='bad', name='Масло Soft99 G0020-001', brand='GIGAS'),
            Product(id='good', name='Свеча DENSO для Toyota', brand='DENSO'),
            Product(id='fit', name='Адаптер для NGK', brand='DENSO')]); db.flush()
        for rid in ('bad', 'good', 'fit'):
            db.add(Review(id=rid, wb_review_id=rid, product_id=rid, rating=5, text=''))
        db.commit()
    result = client.get('/products/page?knowledge=identity').json()
    assert result['total'] == 1
    assert result['items'][0]['id'] == 'bad'
    assert result['items'][0]['brand'] == 'GIGAS'
    assert 'Soft99'.upper() in result['items'][0]['identity_warning']


def test_recent_generation_is_store_scoped_and_excludes_undated_history(client):
    with Session() as db:
        db.add(Product(id='p', name='Test')); db.flush()
        for rid, store, date in [('new', 'owner', None), ('old', 'owner', ''), ('foreign', 'other', None)]:
            db.add(Review(id=rid, wb_review_id=rid, product_id='p', wb_account_id=store, text='', rating=5)); db.flush()
            db.add(Draft(id=rid, review_id=rid, text='saved', original='saved', quality={'passed': True}))
            db.add(GenerationOutcome(job_id='job', review_id=rid, **({'created_at': date} if date is not None else {})))
        db.commit()
        result = recent_generation(db, 'owner')
        assert result['saved'] == result['verified'] == 1


def test_schedule_runtime_distinguishes_due_from_already_queued(client):
    with Session() as db:
        set_setting(db, 'automation_schedule', {'enabled': True, 'questions_sync_enabled': True, 'questions_hours': 3})
        db.add(Job(id='pending', kind='questions', result={'account_id': 'owner'})); db.commit()
    result = client.get('/settings/schedule').json()['runtime']['questions']
    assert result['enabled'] and result['pending_job'] == 'pending'
    assert result['next_eligible_at'] is None
