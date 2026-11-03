"""Server-owned schedules with independent review/question controls."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import select, func
from .db import Job, StoreProfile, Account, setting

DEFAULTS = {'enabled': False, 'reviews_hours': 3, 'questions_hours': 3,
            'products_hours': 24, 'backup_hours': 24, 'drafts_enabled': False,
            'question_drafts_enabled': False, 'drafts_hours': 6, 'auto_propose': False,
            'drafts_all_history': False, 'min_quality': 96, 'max_proposals': 100, 'drafts_days': 7,
            'publication_low_ratings': False, 'publication_all_low_ratings': False, 'publication_enabled': False, 'publication_hours': 1, 'publication_reviews': True, 'publication_questions': True}


def normalize_schedule(raw):
    raw = raw or {}
    value = {**DEFAULTS, **raw}
    markets = raw.get('publication_markets')
    value['publication_markets'] = {provider: bool(markets.get(provider, True)) if isinstance(markets, dict) else True
                                    for provider in ('wb', 'ozon', 'yandex')}
    for kind in ('reviews', 'questions'):
        value[kind + '_hours'] = int(raw.get(kind + '_hours', raw.get('sync_hours', 3)))
        value[kind + '_sync_enabled'] = bool(raw.get(kind + '_sync_enabled', raw.get('sync_enabled', value['enabled'])))
    value['question_drafts_hours'] = int(raw.get('question_drafts_hours', value['drafts_hours']))
    value['sync_hours'] = int(value.get('sync_hours', value['reviews_hours']))
    value['sync_enabled'] = value['reviews_sync_enabled'] or value['questions_sync_enabled']
    value['generation_enabled'] = bool(value['drafts_enabled'] or value['question_drafts_enabled']) and bool(value['enabled'])
    return value


def pending(db, kind, account_id):
    return db.scalar(select(Job.id).where(
        Job.kind == kind, Job.status.in_(('queued', 'running')), Job.cancel_requested == False,
        func.coalesce(Job.result['account_id'].as_string(), 'owner') == account_id).limit(1))


def schedule_status(db, schedule, store_id, current):
    result = {}
    for kind, enabled_key, hours_key in (('reviews', 'reviews_sync_enabled', 'reviews_hours'),
        ('questions', 'questions_sync_enabled', 'questions_hours'), ('drafts', 'drafts_enabled', 'drafts_hours'),
        ('question_drafts', 'question_drafts_enabled', 'question_drafts_hours')):
        running = pending(db, kind, store_id)
        completed = db.scalar(select(Job).where(Job.kind == kind, Job.status == 'completed',
            Job.result['account_id'].as_string() == store_id).order_by(Job.created_at.desc()).limit(1))
        enabled = bool(schedule.get('enabled') and schedule.get(enabled_key))
        last = float(schedule.get(f'last_{kind}:{store_id}', schedule.get('last_' + kind, 0) if store_id == 'owner' else 0))
        due = max(current, last + max(1, int(schedule[hours_key])) * 3600)
        result[kind] = {'enabled': enabled, 'pending_job': running,
            'last_success': ((completed.result or {}).get('finished_at') or completed.created_at) if completed else None,
            'next_eligible_at': datetime.fromtimestamp(due, timezone.utc).isoformat() if enabled and not running else None}
    return result


def enqueue_generation(db, schedule, store, kind, current):
    enabled = schedule.get('question_drafts_enabled') if kind == 'question_drafts' else schedule.get('drafts_enabled')
    if not schedule.get('enabled') or not enabled or not setting(db, 'assistant_enabled', True):
        return False
    refreshing = kind == 'drafts' and db.scalar(select(Job.id).where(Job.kind == 'draft_recheck',
            Job.status.in_(('queued','running')), Job.cancel_requested == False,
            Job.result['account_id'].as_string() == store.id,
            Job.result['policy_regeneration'].as_boolean() == True).limit(1))
    last_key = f'last_{kind}:{store.id}'
    last = float(schedule.get(last_key, schedule.get('last_' + kind, 0) if store.id == 'owner' else 0))
    hours = schedule.get('question_drafts_hours', schedule.get('drafts_hours', 6)) if kind == 'question_drafts' else schedule.get('drafts_hours', 6)
    if current - last < max(1, int(hours)) * 3600 or pending(db, kind, store.id):
        return False
    result = {'account_id': store.id, 'scheduled': True,
              'message': 'Плановая подготовка ответов на вопросы' if kind == 'question_drafts' else 'Плановая подготовка ответов на отзывы'}
    if kind == 'drafts':
        result['since'] = (datetime.fromtimestamp(current, timezone.utc)-timedelta(days=1)).isoformat() if refreshing else '' if schedule.get('drafts_all_history') else (datetime.fromtimestamp(current, timezone.utc) - timedelta(days=int(schedule.get('drafts_days', 7)))).isoformat()
    db.add(Job(id=str(uuid4()), kind=kind, priority=250, result=result))
    schedule[last_key] = current
    return True


def enqueue_due_jobs(db, schedule, current):
    if not schedule.get('enabled'):
        return
    stores = list(db.scalars(select(StoreProfile).join(Account, Account.id == StoreProfile.id).order_by(StoreProfile.created_at)))
    for store in stores:
        if schedule.get('sync_enabled', True):
            for kind in ('products', 'reviews', 'questions'):
                if kind != 'products' and not schedule.get(kind + '_sync_enabled', schedule.get('sync_enabled', True)):
                    continue
                hours = schedule.get(kind + '_hours', schedule.get('sync_hours', 3)) if kind != 'products' else schedule.get('products_hours', 24)
                key = f'last_{kind}:{store.id}'
                last = float(schedule.get(key, schedule.get('last_' + kind, 0) if store.id == 'owner' else 0))
                if current - last >= max(1, int(hours)) * 3600 and not pending(db, kind, store.id):
                    db.add(Job(id=str(uuid4()), kind=kind, priority=200, result={'account_id': store.id, 'scheduled': True}))
                    schedule[key] = current
        for kind in ('drafts', 'question_drafts'):
            enqueue_generation(db, schedule, store, kind, current)
        if store.provider in ('wb', 'ozon', 'yandex'):
            from .scheduled_publication import enqueue
            enqueue(db, schedule, store, current)
