"""Read-only timings of real page serializers; no review text or credentials emitted."""
import json,time
from sqlalchemy import select,text
from app.db import Session,User,StoreProfile
from app.main import stores
from app.services import reviews_page
result={'pages':[]}
with Session() as db:
    db.execute(text('SET LOCAL statement_timeout=15000'))
    owner=db.scalar(select(User).where(User.role=='owner',User.enabled==True))
    assert owner
    started=time.monotonic()
    response=stores({'role':'owner','user_id':owner.id,'permissions':['*'],'store_ids':[]},db)
    result['stores']={'seconds':round(time.monotonic()-started,3),'rows':len(response['items'])}
    db.rollback()
    for store in db.scalars(select(StoreProfile).order_by(StoreProfile.id)):
        started=time.monotonic()
        try:
            db.execute(text('SET LOCAL statement_timeout=15000'))
            response=reviews_page(db,account_id=store.id,limit=30,offset=0)
            result['pages'].append({'market':store.provider,'seconds':round(time.monotonic()-started,3),
                'rows':len(response['items']),'total':response['total']})
        except Exception as error:
            result['pages'].append({'market':store.provider,'seconds':round(time.monotonic()-started,3),'error':type(error).__name__})
        finally:db.rollback()
print(json.dumps(result),flush=True)
