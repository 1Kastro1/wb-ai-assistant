"""Small operational snapshot; no review bodies, secrets or model prompts."""
import time
import json
from datetime import datetime, timezone
from sqlalchemy import select
from .db import Job, StoreProfile, setting, public
from .config import DATA

LABELS = {'products': 'Обновление товаров', 'reviews': 'Обновление отзывов', 'questions': 'Обновление вопросов',
          'drafts': 'Ответы на отзывы', 'question_drafts': 'Ответы на вопросы',
          'single_draft': 'Ответ на отзыв', 'single_question_draft': 'Ответ на вопрос',
          'draft_audit': 'Перепроверка черновиков', 'draft_recheck': 'Проверка правил ответа',
          'knowledge_research': 'Поиск инструкций для важных товаров'}


def queue_overview(db, auth, store_id):
    timestamp = time.time()
    query = select(Job).where(Job.status.in_(('queued', 'running')))
    if auth['role'] != 'owner':
        query = query.where(Job.result['account_id'].as_string() == store_id)
    rows = list(db.scalars(query.order_by(Job.priority, Job.created_at)))
    stores = {s.id: s for s in db.scalars(select(StoreProfile))}
    running = [j for j in rows if j.status == 'running']
    tasks = []
    for job in rows:
        state = job.result or {}
        store = stores.get(state.get('account_id'))
        retry_at = state.get('retry_at') or 0
        waiting = job.status == 'queued'
        blocker = next((j for j in running if j.id != job.id), None)
        reason = ('Повтор после временной ошибки' if retry_at > timestamp else
                  'Выполняется: ' + LABELS.get(blocker.kind, blocker.kind) if waiting and blocker else
                  'Ожидает свободного места в очереди' if waiting else 'Выполняется на сервере')
        tasks.append({'id': job.id, 'kind': job.kind, 'label': LABELS.get(job.kind, job.kind),
                      'store_id': state.get('account_id'), 'store_name': store.name if store else 'Общие задачи',
                      'provider': store.provider if store else '', 'status': job.status,
                      'progress': job.progress, 'reason': reason, 'retry_at': retry_at,
                      'created_at': job.created_at, 'updated_at': state.get('updated_at') or state.get('started_at') or job.created_at,
                      'processed': state.get('processed', state.get('checked', state.get('checkpoint', {}).get('processed', 0))),
                      'total': state.get('total'), 'message': state.get('message', ''),
                      'blocking_job': blocker.id if waiting and blocker else None})
    # Only a latest failed import is an unresolved sync incident. Old failures
    # remain in history after a later successful attempt.
    latest = {}
    for job in db.scalars(select(Job).where(Job.kind.in_(('products', 'reviews', 'questions')),
                         Job.status != 'cancelled',
                         Job.result['account_id'].as_string() == store_id).order_by(Job.created_at.desc())):
        latest.setdefault(job.kind, job)
        if len(latest) == 3:
            break
    incidents = [{'kind': kind, 'message': (job.result or {}).get('message', 'Сбой синхронизации'), 'job_id': job.id}
                 for kind, job in latest.items() if job.status in ('failed', 'interrupted')]
    scheduler = setting(db, 'scheduler_status', {})
    try:
        backup = json.loads((DATA / 'backups/server-status.json').read_text(encoding='utf-8'))
        backup = {key: backup.get(key) for key in ('created_at', 'name', 'size_mb', 'catalog_verified', 'restore_tested')}
    except (OSError, ValueError, TypeError):
        backup = None
    return {'observed_at': datetime.now(timezone.utc).isoformat(), 'tasks': tasks, 'server_backup': backup,
            'running_jobs': len(running), 'queued_jobs': sum(j.status == 'queued' for j in rows),
            'retry_jobs': sum((j.result or {}).get('retry_at', 0) > timestamp for j in rows),
            'sync_incidents': incidents,
            'needs_attention': bool(incidents or scheduler.get('state') == 'error')}
