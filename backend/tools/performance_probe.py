"""Read-only timings and query plans; prints no credentials or customer messages."""
import asyncio
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, text
from app.db import Session, Product, Review, Draft, Job


def probe():
    result = {}
    with Session() as db:
        if db.bind.dialect.name == 'postgresql':
            db.execute(text('SET LOCAL statement_timeout = 10000'))
            for key, sql in {
                'review_list': "SELECT id FROM reviews WHERE wb_account_id='owner' ORDER BY created_at DESC,id DESC LIMIT 30",
                'queue': "SELECT id FROM jobs WHERE status='queued' ORDER BY priority,created_at LIMIT 1",
            }.items():
                result[key] = db.execute(text('EXPLAIN (ANALYZE, FORMAT JSON) ' + sql)).scalar()
        samples = []
        for _ in range(5):
            started = time.monotonic()
            db.execute(text('SELECT 1')).scalar()
            samples.append(round((time.monotonic() - started) * 1000, 2))
        result['database_select_ms'] = samples
        db.rollback()
    return result


async def check_rules():
    from app.owner_reply_rules import matching_owner_rules
    from app.owner_reply_policy import enforce_owner_rules
    from app.services import trusted_product_facts, assess_reply
    with Session() as db:
        product = db.scalars(select(Product).where(Product.id.like('ozon:%190445011%'))).first()
        review = db.scalars(select(Review).where(Review.product_id == product.id, Review.rating == 5,
            Review.text == '').order_by(Review.created_at.desc())).first() if product else None
        draft = db.scalar(select(Draft).where(Draft.review_id == review.id)) if review else None
        if not draft:
            return {'found': False}
        rules = matching_owner_rules(db, product)
        facts = trusted_product_facts(db, product)
        started = time.monotonic()
        _, quality = await enforce_owner_rules(draft.text, review, product, rules, facts,
            lambda answer: assess_reply(review, product, answer, facts), check_seconds=600)
        return {'found': True, 'seconds': round(time.monotonic() - started, 2), 'quality': quality}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(check_rules()) if '--rules' in sys.argv else probe(), ensure_ascii=True))

