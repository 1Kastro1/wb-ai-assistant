"""Read-only phase timings to isolate remaining list latency."""
import json,time
from sqlalchemy import select,func,text
from app.db import Session,Review,StoreProfile
from app.services import _review_query,review_items
result=[]
with Session() as db:
    for store in list(db.scalars(select(StoreProfile).order_by(StoreProfile.id))):
        item={'market':store.provider}
        db.execute(text('SET LOCAL statement_timeout=15000'))
        filters={'account_id':store.id,'limit':30,'offset':0}
        started=time.monotonic()
        rows=list(db.scalars(_review_query(filters).order_by(Review.created_at.desc(),Review.id).limit(30)))
        item['rows_seconds']=round(time.monotonic()-started,3)
        started=time.monotonic();review_items(db,rows)
        item['serialize_seconds']=round(time.monotonic()-started,3)
        started=time.monotonic()
        item['total']=db.scalar(_review_query(filters).with_only_columns(func.count()).order_by(None))
        item['count_seconds']=round(time.monotonic()-started,3)
        started=time.monotonic()
        direct=db.scalar(select(func.count()).select_from(Review).where(Review.wb_account_id==store.id,Review.rating<=5,~Review.marketplace.like('%_question')))
        item['direct_count_seconds']=round(time.monotonic()-started,3)
        assert direct==item['total']
        result.append(item);db.rollback()
print(json.dumps(result),flush=True)
