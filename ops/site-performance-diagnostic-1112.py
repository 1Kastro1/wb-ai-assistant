"""Bounded read-only measurements; no credentials or customer text are emitted."""
import json,time
from sqlalchemy import select,func,text,event,inspect
from app.db import Session,engine,Product,Review,StoreProfile,Memory
from app.shared_marketplaces import equivalent_products
from app.owner_reply_rules import matching_owner_rules
result={'lookups':[],'counts':[]}
with Session() as db:
    db.execute(text('SET LOCAL statement_timeout=15000'))
    result['products']=db.scalar(select(func.count()).select_from(Product))
    result['sizes_mb']=dict(db.execute(text("SELECT relname,pg_total_relation_size(oid)/1048576 FROM pg_class WHERE relname IN ('products','reviews','generated_answers')")).all())
    result['active']=list(db.execute(text("SELECT coalesce(wait_event_type,'running'),count(*) FROM pg_stat_activity WHERE state='active' GROUP BY 1")).all())
    result['indexes']=inspect(engine).get_indexes('products')
    stores=list(db.scalars(select(StoreProfile).order_by(StoreProfile.id)))
    db.rollback()
    for store in stores:
        started=time.monotonic()
        db.execute(text('SET LOCAL statement_timeout=15000'))
        try:
            count=db.scalar(select(func.count()).select_from(Review).where(Review.wb_account_id==store.id,~Review.marketplace.like('%_question')))
            result['counts'].append({'market':store.provider,'rows':count,'seconds':round(time.monotonic()-started,3)})
        except Exception as error:result['counts'].append({'market':store.provider,'error':type(error).__name__})
        db.rollback()
        products=list(db.scalars(select(Product).join(Review,Review.product_id==Product.id).where(
            Review.wb_account_id==store.id).order_by(Review.created_at.desc()).limit(2)))
        for product in products[:1]:
            started=time.monotonic();queries=[]
            def record(conn,cursor,statement,parameters,context,many):
                if statement.lstrip().startswith('SELECT products.'):queries.append((statement,parameters))
            event.listen(engine,'before_cursor_execute',record)
            item={'market':store.provider}
            try:
                db.execute(text('SET LOCAL statement_timeout=15000'))
                matches=equivalent_products(db,product)
                item.update(seconds=round(time.monotonic()-started,3),matches=len(matches))
                if queries:
                    statement,parameters=queries[-1]
                    item['plan']=[r[0] for r in db.connection().exec_driver_sql('EXPLAIN '+statement,parameters).all()]
            except Exception as error:item.update(seconds=round(time.monotonic()-started,3),error=type(error).__name__)
            finally:event.remove(engine,'before_cursor_execute',record);db.rollback()
            result['lookups'].append(item)
print(json.dumps(result,ensure_ascii=False,default=str),flush=True)
