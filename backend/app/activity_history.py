"""Readable audit details, with no generic request-body logging."""
from sqlalchemy import select, or_
from .db import Activity, User, public

RULE_EVENTS = {'rule_proposed','rule_updated','rule_added','rule_deleted','rule_rejected'}


def rule_details(payload, event):
    if event not in RULE_EVENTS:
        return {}
    return {'event':event,'rule':str(payload.get('text') or '')[:2000],
            'scope':str(payload.get('scope') or 'global')[:200],
            'kind':str(payload.get('kind') or 'GLOBAL_RULE')[:100]}


def activity_page(db, auth, q='', user_id='', before=0, limit=50):
    limit = max(1,min(int(limit),100))
    statement = select(Activity,User.display_name).outerjoin(User,User.id==Activity.user_id)
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
    items = [{**public(row),'actor_name':name or row.username} for row,name in rows[:limit]]
    return {'items':items,'has_more':len(rows)>limit,'next_before':items[-1]['id'] if items else None}
