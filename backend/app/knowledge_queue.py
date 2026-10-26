"""Search urgent products in small durable steps; found pages need approval."""
import asyncio
from sqlalchemy import select, func, case
from .db import Session, Review, Product, Job, now
from .product_search import knowledge_condition


def urgent_product_ids(db, store_id, limit=50):
    priority = select(Review.product_id, func.count().label('pending'),
        func.max(case((Review.marketplace.like('%_question'), 1), else_=0)).label('question')).where(
        Review.wb_account_id == store_id, Review.is_answered == False,
        (Review.marketplace.like('%_question')) | (Review.rating <= 3)).group_by(Review.product_id).subquery()
    return list(db.scalars(select(Product.id).join(priority, priority.c.product_id == Product.id).where(
        ~knowledge_condition(db)).order_by(priority.c.question.desc(), priority.c.pending.desc(), Product.id).limit(limit)))


async def research_step(job_id, session_factory=Session):
    from .main import web_research
    from .verified_instructions import attach_verified_instructions
    from .services import remember_web_instruction_sources
    with session_factory() as db:
        job = db.get(Job, job_id); state = dict(job.result or {})
        if job.cancel_requested:
            job.status = 'cancelled'; db.commit(); return
        cursor, ids = int(state.get('cursor', 0)), state.get('product_ids', [])
        product = db.get(Product, ids[cursor]) if cursor < len(ids) else None
        found, saved, error = 0, 0, ''
        if product:
            saved = attach_verified_instructions(db, product)
            db.commit()
            try:
                sources = await asyncio.wait_for(web_research.search_instructions(product, db, 'инструкция применения'), 15)
                # Merge against fresh facts; an owner's concurrent edits win.
                db.refresh(product)
                if sources:
                    remember_web_instruction_sources(product, [{**item, 'kind': 'instruction'} for item in sources])
                    found = len(sources)
                    db.commit()
            except (asyncio.TimeoutError, ValueError) as exc:
                error = type(exc).__name__
        db.refresh(job)
        state.update(cursor=cursor + int(cursor < len(ids)), processed=cursor + int(cursor < len(ids)),
                     total=len(ids), found=state.get('found', 0) + found,
                     saved_instructions=state.get('saved_instructions', 0) + saved,
                     search_errors=state.get('search_errors', 0) + int(bool(error)), updated_at=now())
        state['message'] = f"Проверено товаров: {state['processed']} из {len(ids)}. Найденные интернет-источники ожидают проверки владельцем."
        job.result = state
        job.progress = round(100 * state['processed'] / max(1, len(ids)))
        job.status = 'cancelled' if job.cancel_requested else 'completed' if state['processed'] >= len(ids) else 'queued'
        db.commit()
