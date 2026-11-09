"""Read seller comments, never infer a seller reply from the comment count."""
import argparse
import asyncio
import json
from sqlalchemy import select, or_
from .db import Session, Review, Draft, Publication, StoreProfile, User, setting, set_setting, now
from .security import guard
from .safety import mask_vin


async def seller_reply(headers, review_id, cancelled=None, *, expected_text=None, comment_id=None):
    offset, seen, pending, rejected, mismatch = 0, set(), None, None, False
    for _ in range(100):
        if cancelled and cancelled():
            raise asyncio.CancelledError()
        data = await guard.request('ozon', 'review_comments', headers=headers,
            body={'review_id':str(review_id), 'sort_dir':'DESC', 'limit':100, 'offset':offset})
        payload = data.get('result', data)
        comments = payload.get('comments')
        if not isinstance(comments, list):
            raise ValueError('Ozon не вернул список комментариев; статус ответа сохранён')
        published, unknown = [], False
        for comment in comments:
            if comment.get('is_owner') is not True or comment.get('parent_comment_id'):
                continue
            if comment.get('review_id') and str(comment['review_id']) != str(review_id):
                raise ValueError('Ozon вернул комментарий другого отзыва')
            text = str(comment.get('text') or '').strip()
            status = str(comment.get('status') or '').upper()
            if expected_text is not None:
                # The publication snapshot is immutable; a later unrelated
                # seller comment must never confirm this particular attempt.
                same_id = not comment_id or str(comment.get('id') or comment.get('comment_id') or '') == str(comment_id)
                if text != expected_text.strip() or not same_id:
                    mismatch = True
                    continue
            if comment.get('is_deleted') is True or status == 'DELETED':
                continue
            if 'is_published' in comment or 'is_rejected' in comment:
                live, refused = comment.get('is_published'), comment.get('is_rejected')
                if type(live) is not bool or type(refused) is not bool or (live and refused):
                    unknown = True; continue
                status = 'PUBLISHED' if live else 'REJECTED' if refused else 'ON_MODERATION'
            if not text:
                unknown = True; continue
            if status in ('', 'PUBLISHED', 'APPROVED'):
                if expected_text is not None and not status and not comment.get('published_at'):
                    unknown = True; continue
                published.append(comment)
            elif status in ('PENDING', 'MODERATION', 'ON_MODERATION', 'PROCESSING'):
                pending = pending or {'state':'answer_pending', 'text':text}
            elif status in ('REJECTED', 'DECLINED'):
                rejected = rejected or {'state':'answer_rejected', 'text':text}
            else:
                unknown = True
        if published:
            value = max(published, key=lambda c: str(c.get('published_at') or ''))
            return {'state':'published', 'text':str(value['text']).strip()}
        if unknown:
            raise ValueError('Статус комментария продавца Ozon не распознан; нужна сверка')
        if payload.get('has_next') is False or (len(comments) < 100 and not payload.get('has_next')):
            return pending or rejected or {'state':'mismatch' if mismatch else 'absent', 'text':''}
        next_offset = payload.get('offset', offset + len(comments))
        if not isinstance(next_offset, int) or next_offset <= offset or next_offset in seen:
            raise ValueError('Ozon повторил страницу комментариев; статус ответа сохранён')
        seen.add(offset); offset = next_offset
    raise ValueError('Список комментариев Ozon не завершён; статус ответа сохранён')


def snapshot(row):
    return row.is_answered, row.existing_answer, row.status


def apply_reply(db, rid, expected, result):
    db.expire_all()
    row = db.scalar(select(Review).where(Review.id == rid).with_for_update())
    if not row or snapshot(row) != expected or row.status == 'publishing':
        db.rollback(); return 'changed'
    reservation = db.scalar(select(Publication).where(Publication.review_id == rid))
    if reservation and reservation.status in ('in_flight', 'uncertain', 'mismatch', 'answer_pending'):
        db.rollback(); return 'reserved'
    state = result['state']
    if state == 'absent' and reservation and reservation.status == 'published':
        # A remote visibility delay must never enable duplicate publication.
        row.status = 'answer_unknown'
        row.is_answered = True
        db.commit(); return 'answer_unknown'
    row.is_answered = state in ('published', 'answer_pending')
    row.existing_answer = mask_vin(result['text'])
    row.status = state if state != 'absent' else ('manual_review' if row.manual else
        'draft_ready' if db.scalar(select(Draft.id).where(Draft.review_id == rid)) else 'unanswered')
    db.commit()
    from .read_cache import clear_reads
    clear_reads()
    return state


async def sync_reviews(provider, db, store_id, progress=None, cancelled=None, resume=None, checkpoint=None, between_pages=None):
    from .integrations import store_credentials, upsert_market_review
    store, token = store_credentials(db, store_id)
    headers = provider.headers(store, token)
    total, last_id = int((resume or {}).get('processed',0)), str((resume or {}).get('last_id',''))
    # The API page cursor may be opaque. Keep that cursor until the whole page
    # is done, and record completed review IDs so a cooperative yield after one
    # comment lookup resumes the same page without skipping or repeating work.
    page_done = set(str(value) for value in (resume or {}).get('page_done_ids', []))
    seen = {last_id} if last_id else set()
    for _ in range(10000):
        if cancelled and cancelled(): raise asyncio.CancelledError()
        body = {'limit':100, 'sort_dir':'DESC'}
        if last_id: body['last_id'] = last_id
        response = await guard.request('ozon','reviews',headers=headers,body=body)
        payload = response.get('result',response)
        items = payload.get('reviews')
        if not isinstance(items,list): raise ValueError('Ozon не вернул список отзывов')
        for item in items:
            if cancelled and cancelled(): raise asyncio.CancelledError()
            if str(item['id']) in page_done:
                continue
            previous = db.scalar(select(Review).where(Review.wb_account_id==store_id, Review.wb_review_id==str(item['id'])))
            saved = snapshot(previous) if previous else (False,'','unanswered')
            content = '\n'.join(str(item.get(k) or '') for k in ('text','positive','negative')).strip()
            row = upsert_market_review(db, store, item['id'],item.get('sku') or item.get('product_id') or 'unknown',
                item.get('product_name') or item.get('sku_name'),'',item.get('rating'),content,
                item.get('published_at') or item.get('created_at'),saved[0],saved[1])
            if saved[2] in ('publishing','answer_pending','answer_unknown'):
                row.status = saved[2]
            expected, rid = snapshot(row), row.id
            db.commit()  # Do not hold row locks while awaiting Ozon.
            raw_count = item.get('comments_amount')
            zero = (type(raw_count) is int and raw_count == 0) or raw_count == '0'
            evidence = {'state':'absent','text':''} if zero and not saved[0] and not saved[1] else await seller_reply(headers,item['id'],cancelled)
            apply_reply(db,rid,expected,evidence)
            total += 1
            page_done.add(str(item['id']))
            if checkpoint: checkpoint({'last_id': last_id, 'processed': total,
                                       'page_done_ids': sorted(page_done)})
        if between_pages: await between_pages()
        if progress: progress(min(95,5+total//100))
        if not items or payload.get('has_next') is False or (len(items)<100 and not payload.get('has_next')):
            return total
        last_id = str(payload.get('last_id') or items[-1]['id'])
        if not last_id or last_id in seen: raise ValueError('Ozon повторил страницу отзывов; прогресс сохранён')
        seen.add(last_id)
        page_done.clear()
        if checkpoint: checkpoint({'last_id':last_id, 'processed':total})
    raise ValueError('Достигнут лимит страниц отзывов Ozon; прогресс сохранён')


async def backfill(store_id, actor_id, limit=50):
    """Restartable local repair. Reads Ozon only; never regenerates or sends."""
    from .integrations import store_credentials, ozon
    from .process_lock import ProcessLock
    async with ProcessLock('ozon-replies-' + store_id):
        with Session() as db:
            actor, store = db.get(User,actor_id), db.get(StoreProfile,store_id)
            if not actor or not actor.enabled or actor.role!='owner' or not store or store.provider!='ozon':
                raise ValueError('Нужен владелец и магазин Ozon')
            store, token = store_credentials(db,store_id)
            headers = ozon.headers(store,token)
            key = 'ozon-reply-backfill:' + store_id
            state = dict(setting(db,key,{}) or {})
            ids = list(db.scalars(select(Review.id).where(Review.wb_account_id==store_id, Review.marketplace=='ozon',
                Review.id > state.get('cursor',''), or_((Review.is_answered==True) & (Review.existing_answer==''),
                Review.status.in_(('answer_pending','answer_unknown')))).order_by(Review.id).limit(limit)))
            for rid in ids:
                row = db.get(Review,rid); expected, remote_id = snapshot(row), row.wb_review_id
                db.commit()
                evidence = await seller_reply(headers,remote_id)
                outcome = apply_reply(db,rid,expected,evidence)
                state.update(cursor=rid,checked=state.get('checked',0)+1,updated_at=now())
                state[outcome] = state.get(outcome,0)+1
                set_setting(db,key,state); db.commit()
                await asyncio.sleep(.2)
            state['complete'] = len(ids)<limit
            set_setting(db,key,state);db.commit()
            return state


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--store',required=True);parser.add_argument('--actor',required=True)
    parser.add_argument('--limit',type=int,default=50);args=parser.parse_args()
    print(json.dumps(asyncio.run(backfill(args.store,args.actor,max(1,min(500,args.limit))))),flush=True)
