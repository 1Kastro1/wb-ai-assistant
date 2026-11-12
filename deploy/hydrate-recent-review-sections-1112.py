"""One bounded read per recent API page; enrich only unchanged existing reviews.

Does not import new reviews, update text/status, enable schedules or publish.
Run inside the assistant backend after migration 0019.
"""
import asyncio
import json
from sqlalchemy import select
from app.db import Session,StoreProfile,Review
from app.integrations import store_credentials,OzonProvider,YandexProvider
from app.security import guard
from app.review_sections import review_sections
from app.safety import mask_vin


def save(store_id,market,items):
    changed=[]
    for item in items:
        source=(item.get('description') or {}) if market=='yandex' else item
        parts=review_sections(source,market)
        order={'wb':('text','pros','cons'),'ozon':('text','positive','negative'),
            'yandex':('advantages','disadvantages','comment')}[market]
        flat=mask_vin('\n'.join(str(source.get(key) or '') for key in order).strip())
        external_id=item.get('feedbackId') if market=='yandex' else item.get('id')
        if not parts or external_id is None:continue
        with Session() as db:
            row=db.scalar(select(Review).where(Review.wb_account_id==store_id,
                Review.wb_review_id==str(external_id),Review.marketplace==market).with_for_update())
            # Reject updated API text: the original draft/audit input remains
            # unchanged while imports are paused.
            if not row or row.text.strip()!=flat or row.sections==parts:continue
            row.sections=parts
            changed.append({'id':row.id,'product_id':row.product_id,'created_at':row.created_at})
            db.commit()
    return changed


async def main():
    results=[]
    with Session() as db:
        ids=list(db.scalars(select(StoreProfile.id).where(StoreProfile.provider.in_(('yandex','wb','ozon')))))
    for sid in ids:
        try:
            with Session() as db:store,token=store_credentials(db,sid)
            market=store.provider
            batches=[]
            if market=='yandex':
                raw=await guard.request('yandex','reviews',headers={'Api-Key':token},
                    url=YandexProvider().target(store,'reviews'),params={'limit':50},body={'reactionStatus':'ALL'})
                batches.append((raw.get('result') or {}).get('feedbacks') or [])
            elif market=='ozon':
                raw=await guard.request('ozon','reviews',headers=OzonProvider().headers(store,token),body={'limit':100,'sort_dir':'DESC'})
                batches.append(raw.get('result',raw).get('reviews') or [])
            else:
                for answered in (False,True):
                    raw=await guard.request('wb','reviews',token=token,
                        params={'isAnswered':str(answered).lower(),'take':100,'skip':0,'order':'dateDesc'})
                    if raw.get('error'):raise ValueError('marketplace response error')
                    batches.append((raw.get('data') or {}).get('feedbacks') or [])
            changed=[]
            for batch in batches:changed.extend(save(sid,market,batch))
            results.append({'marketplace':market,'store':sid,'updated':len(changed),'samples':changed[:3]})
        except Exception as exc:
            # Never print a request, token, response body or credentials.
            results.append({'store':sid,'error':type(exc).__name__})
    print(json.dumps(results,ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
