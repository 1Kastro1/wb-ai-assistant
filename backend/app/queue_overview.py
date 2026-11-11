"""Small operational snapshot; no review bodies, secrets or model prompts."""
import time
import json
from datetime import datetime, timezone
from sqlalchemy import select, func
from .db import Job, StoreProfile, setting, public
from .config import DATA

LABELS = {'publication_reconcile': 'Сверка отправленных ответов', 'scheduled_publication': 'Автоматическая выгрузка готовых ответов', 'products': 'Обновление товаров', 'reviews': 'Обновление отзывов', 'questions': 'Обновление вопросов',
          'drafts': 'Ответы на отзывы', 'question_drafts': 'Ответы на вопросы',
          'single_draft': 'Ответ на отзыв', 'single_question_draft': 'Ответ на вопрос',
          'draft_audit': 'Перепроверка черновиков', 'draft_recheck': 'Проверка правил ответа',
          'knowledge_research': 'Поиск инструкций для важных товаров',
          'wb_publish_prepare': 'Список ответов для маркетплейса',
          'wb_exclusions_audit': 'Причины исключения ответов Wildberries',
          'wb_excluded_recovery': 'Новые ответы на исключённые отзывы Wildberries',
          'wb_publish_send': 'Публикация подтверждённых ответов'}


def job_stage(kind,state):
    if kind in ('scheduled_publication','wb_publish_send','wb_publish_prepare','publication_reconcile'):return 'publication'
    if kind in ('products','reviews','questions'):return 'sync'
    if kind=='draft_recheck' and not state.get('policy_regeneration') or kind=='draft_audit':return 'verification'
    return 'generation'


def queue_overview(db, auth, store_id):
    timestamp = time.time()
    query = select(Job).where(Job.status.in_(('queued', 'running')), Job.cancel_requested == False)
    if auth['role'] != 'owner':
        query = query.where(Job.result['account_id'].as_string() == store_id)
    from .queue_policy import queue_ordering
    rows = list(db.scalars(query.order_by((Job.status == 'running').desc(),
        (func.coalesce(Job.result['retry_at'].as_float(), 0) > timestamp), *queue_ordering())))
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
        if job.kind == 'wb_excluded_recovery':
            reason = 'Приоритет Wildberries. ' + reason
        tasks.append({'id': job.id, 'kind': job.kind, 'label': 'Обновление ответов по правилам' if state.get('policy_regeneration') else LABELS.get(job.kind, job.kind),
                      'store_id': state.get('account_id'), 'store_name': store.name if store else 'Общие задачи',
                      'provider': store.provider if store else '', 'status': job.status,
                      'stage_key': job_stage(job.kind,state), 'last_progress_at':state.get('last_progress_at'),
                      'last_served_at':state.get('last_served_at'), 'started_at':state.get('started_at'),
                      'detail':state.get('detail',''), 'progress': job.progress, 'reason': reason, 'retry_at': retry_at,
                      'created_at': job.created_at, 'updated_at': state.get('last_progress_at') or state.get('updated_at') or state.get('last_served_at') or state.get('started_at') or job.created_at,
                      'processed': state.get('processed', state.get('checked', state.get('checkpoint', {}).get('processed', 0))),
                      'total': state.get('total'), 'message': state.get('message', ''),
                      'saved': state.get('saved') if state.get('policy_regeneration') else state.get('processed') if job.kind in ('drafts', 'question_drafts', 'single_draft', 'single_question_draft') else None,
                      'verified': state.get('verified'), 'failed': state.get('failed', 0),
                      'attention': state.get('attention', 0), 'deferred': state.get('deferred', 0),
                      'stage': 'Сверка результата отправки' if job.kind == 'publication_reconcile' else 'Отправка готовых ответов' if job.kind == 'scheduled_publication' else 'Проверка сохранённого текста' if state.get('check_only') or state.get('positive_recheck') else 'Генерация ответов' if 'draft' in job.kind else 'Синхронизация',
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
    from .generation_monitor import recent_generation
    from .pipeline_overview import overview
    visible_stores=stores if auth['role']=='owner' else {k:v for k,v in stores.items() if k==store_id}
    return {'observed_at': datetime.now(timezone.utc).isoformat(), 'tasks': tasks, 'server_backup': backup,
            'generation_window': recent_generation(db, store_id), 'market_processes':overview(db,visible_stores),
            'running_jobs': len(running), 'queued_jobs': sum(j.status == 'queued' for j in rows),
            'retry_jobs': sum((j.result or {}).get('retry_at', 0) > timestamp for j in rows),
            'sync_incidents': incidents,
            'needs_attention': bool(incidents or scheduler.get('state') == 'error')}

