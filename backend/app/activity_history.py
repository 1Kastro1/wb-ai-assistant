"""Readable audit details, with no generic request-body logging."""
from sqlalchemy import select, or_, and_
from .db import Activity, User, public

RULE_EVENTS = {'rule_proposed','rule_updated','rule_added','rule_deleted','rule_rejected'}


def important_activity():
    """Filter the readable feed in SQL; retain the complete audit in the DB."""
    event = Activity.details['event'].as_string()
    changes = or_(
        event.in_(RULE_EVENTS | {'rule_enabled', 'rule_disabled', 'registration_accepted',
                               'registration_rejected', 'user_deleted', 'product_knowledge_refreshed'}),
        Activity.path.in_(['/stores', '/users', '/auth/password', '/actions/publish',
                          '/auto-replies/start', '/auto-replies/pause', '/jobs/drafts',
                          '/jobs/selected-drafts', '/jobs/question-drafts',
                          '/memory', '/memory/propose', '/backup', '/portable/export', '/portable/sync-now',
                          '/portable/restore', '/fitment/catalogs/import', '/catalog/import']),
        and_(Activity.path.startswith('/settings/'),
             ~Activity.path.in_(['/settings/wb-test']), ~Activity.path.endswith('/test')),
        and_(Activity.path.startswith('/stores/'), ~Activity.path.endswith('/activate'),
             ~Activity.path.endswith('/test')),
        Activity.path.startswith('/users/'),
        and_(Activity.path.startswith('/drafts/'), Activity.method.in_(['PATCH', 'DELETE'])),
        and_(Activity.path.startswith('/memory/'), Activity.method.in_(['PATCH', 'DELETE'])),
        Activity.path.startswith('/jobs/sync/'),
        and_(Activity.path.startswith('/jobs/'),
             or_(Activity.path.endswith('/cancel'), Activity.path.endswith('/retry'))),
        and_(Activity.path.startswith('/actions/'), Activity.method == 'POST',
             or_(Activity.path.endswith('/confirm'), Activity.path.endswith('/cancel'))),
        and_(Activity.path.startswith('/products/'), Activity.path.contains('/facts')),
        and_(Activity.path.startswith('/fitment/catalogs/'), Activity.path.endswith('/enabled')),
        Activity.path == '/fitment/internet/settings',
    )
    return or_(Activity.status >= 400, changes)


def rule_details(payload, event):
    if event not in RULE_EVENTS:
        return {}
    return {'event':event,'rule':str(payload.get('text') or '')[:2000],
            'scope':str(payload.get('scope') or 'global')[:200],
            'kind':str(payload.get('kind') or 'GLOBAL_RULE')[:100]}


def activity_page(db, auth, q='', user_id='', before=0, limit=50):
    limit = max(1,min(int(limit),100))
    statement = select(Activity,User.display_name).outerjoin(User,User.id==Activity.user_id).where(important_activity())
    if auth['role'] != 'owner':
        statement = statement.where(Activity.user_id==auth['user_id'])
    if user_id:
        statement = statement.where(Activity.user_id==user_id)
    if before:
        statement = statement.where(Activity.id<before)
    if q.strip():
        term = q.strip()[:200]
        statement = statement.where(or_(Activity.username.contains(term,autoescape=True),
            User.display_name.contains(term,autoescape=True),Activity.details['rule'].as_string().contains(term,autoescape=True)))
    rows = db.execute(statement.order_by(Activity.id.desc()).limit(limit+1)).all()
    items = [{**public(row),'actor_name':name or ('Система' if row.user_id == 'system' else row.username)} for row,name in rows[:limit]]
    return {'items':items,'has_more':len(rows)>limit,'next_before':items[-1]['id'] if items else None}
