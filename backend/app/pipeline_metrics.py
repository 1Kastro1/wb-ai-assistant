"""Measured end-to-end latency; historical records without evidence stay absent."""
import math
from datetime import datetime
from sqlalchemy import select
from .db import Review, Publication


def delivery_latency(db, store_id):
    rows=db.execute(select(Review.received_at,Publication.confirmed_at)
        .join(Publication,Publication.review_id==Review.id).where(
            Review.wb_account_id==store_id,Review.marketplace.in_(('wb','ozon','yandex')),
            Review.received_at!='',Publication.confirmed_at!='',Publication.status=='published')
        .order_by(Publication.confirmed_at.desc()).limit(100)).all()
    seconds=[]
    for received,sent in rows:
        try:
            value=(datetime.fromisoformat(sent)-datetime.fromisoformat(received)).total_seconds()
        except (ValueError,TypeError):continue
        if value>=0:seconds.append(value)
    seconds.sort()
    return {'samples':len(seconds),
        'p50_seconds':round(seconds[math.ceil(len(seconds)*.5)-1],1) if seconds else None,
        'p95_seconds':round(seconds[math.ceil(len(seconds)*.95)-1],1) if seconds else None}
