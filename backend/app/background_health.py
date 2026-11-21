"""Check that the worker is scheduling, the model responds, and work advances."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .db import Job, setting


def background_health(db, current=None):
    current = current or datetime.now(timezone.utc)
    state = setting(db, 'worker_heartbeat', {})
    worker = bool(state.get('at', '') >= (current - timedelta(minutes=2)).isoformat()
                  and state.get('scheduler') == 'ok')
    model = worker and state.get('model') == 'ok'
    cutoff = (current - timedelta(minutes=15)).isoformat()
    served = func.coalesce(Job.result['last_served_at'].as_string(), Job.created_at)
    progress = func.coalesce(Job.result['last_progress_at'].as_string(), served)
    stalled = db.scalar(select(Job.id).where(Job.status == 'running',
        Job.cancel_requested == False, served < cutoff, progress < cutoff).limit(1)) if worker else None
    queue = worker and not stalled
    return {'status': 'ok' if worker and model and queue else 'degraded',
        'worker': 'ok' if worker else 'unavailable',
        'model': 'ok' if model else 'unavailable',
        'queue': 'ok' if queue else 'stalled' if stalled else 'unavailable'}
