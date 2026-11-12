"""Time read-only dashboard serializers, emitting SQL structure without parameters."""
import json,time
from sqlalchemy import select,text,event
from app.db import Session,User,engine
from app.main import system_status,reply_quality,marketplace_stats,stats
for label,fn in [('system',system_status),('quality',reply_quality),('marketplaces',marketplace_stats),('analytics',stats)]:
    timings=[]
    def before(conn,cursor,statement,parameters,context,many):
        context.perf_started=time.monotonic()
    def after(conn,cursor,statement,parameters,context,many):
        seconds=time.monotonic()-context.perf_started
        if seconds>.2:timings.append({'seconds':round(seconds,3),'sql':' '.join(statement.split())[:900]})
    event.listen(engine,'before_cursor_execute',before);event.listen(engine,'after_cursor_execute',after)
    started=time.monotonic();result={'section':label}
    try:
        with Session() as db:
            db.execute(text('SET LOCAL statement_timeout=15000'))
            owner=db.scalar(select(User).where(User.role=='owner',User.enabled==True))
            auth={'role':'owner','user_id':owner.id,'permissions':['*'],'store_ids':[]}
            value=fn(auth,db)
            result['ok']=True
    except Exception as error:result['error']=type(error).__name__
    finally:
        event.remove(engine,'before_cursor_execute',before);event.remove(engine,'after_cursor_execute',after)
    result.update(seconds=round(time.monotonic()-started,3),slow=sorted(timings,key=lambda r:-r['seconds'])[:4])
    print(json.dumps(result),flush=True)
