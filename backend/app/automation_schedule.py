"""Server-owned schedules with independent review/question controls."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import select, func
from .db import Job, StoreProfile, Account, setting

DEFAULTS = {'enabled': False, 'reviews_hours': 3, 'questions_hours': 3,
            'products_hours': 24, 'backup_hours': 24, 'drafts_enabled': False,
            'question_drafts_enabled': False, 'drafts_hours': 6, 'auto_propose': False,
            'min_quality': 96, 'max_proposals': 100, 'drafts_days': 7}


def normalize_schedule(raw):
    raw = raw or {}
    value = {**DEFAULTS, **raw}
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


def enqueue_generation(db, schedule, store, kind, current):
    enabled = schedule.get('question_drafts_enabled') if kind == 'question_drafts' else schedule.get('drafts_enabled')
    if not schedule.get('enabled') or not enabled or not setting(db, 'assistant_enabled', True):
        return False
    last_key = f'last_{kind}:{store.id}'
    last = float(schedule.get(last_key, schedule.get('last_' + kind, 0) if store.id == 'owner' else 0))
    hours = schedule.get('question_drafts_hours', schedule.get('drafts_hours', 6)) if kind == 'question_drafts' else schedule.get('drafts_hours', 6)
    if current - last < max(1, int(hours)) * 3600 or pending(db, kind, store.id):
        return False
    result = {'account_id': store.id, 'scheduled': True,
              'message': 'Плановая подготовка ответов на вопросы' if kind == 'question_drafts' else 'Плановая подготовка ответов на отзывы'}
    if kind == 'drafts':
        result['since'] = (datetime.fromtimestamp(current, timezone.utc) - timedelta(days=int(schedule.get('drafts_days', 7)))).isoformat()
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
