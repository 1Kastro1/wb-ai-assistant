"""Owner-only diagnosis. Default reads only; --send-one uses normal approval gates."""
import argparse
import asyncio
import json
import httpx
from sqlalchemy import select
from .db import Session,User,StoreProfile,Review,Draft,Publication,Action,setting
from .integrations import store_credentials,ozon
from .ozon_reviews import seller_reply
from .security import redact


async def inspect(store_id,actor_id,send_one=False):
    from .scheduled_publication import candidate_query,eligibility_reason,blocked_reason,market_enabled
    from .services import propose_publish,confirm_action
    with Session() as db:
        actor,store=db.get(User,actor_id),db.get(StoreProfile,store_id)
        if not actor or not actor.enabled or actor.role!='owner' or not store or store.provider!='ozon':
            raise ValueError('Active owner and Ozon store required')
        account,token=store_credentials(db,store_id)
        headers=ozon.headers(account,token)
        rows=list(db.execute(select(Publication,Review,Action).join(Review,Review.id==Publication.review_id)
            .join(Action,Action.id==Publication.action_id).where(Review.wb_account_id==store_id,
            Review.marketplace=='ozon',Publication.status=='uncertain').order_by(Action.created_at.desc()).limit(3)))
        for pub,review,action in rows:
            answer=next((i['text'] for i in action.payload.get('items',[]) if i['review_id']==review.id),'')
            evidence=await seller_reply(headers,review.wb_review_id)
            print(json.dumps({'review_id':review.id,'remote_state':evidence['state'],
                'matches_attempt':bool(evidence['text'] and evidence['text']==answer)},ensure_ascii=False),flush=True)
        schedule=setting(db,'automation_schedule',{})
        if not schedule.get('enabled') or not schedule.get('publication_enabled') or not market_enabled(schedule,'ozon') or blocked_reason(db,schedule):
            raise ValueError('Owner automatic publication authorization is not active')
        selected=None
        for review in db.scalars(candidate_query(store_id,schedule).where(Review.rating==5,Review.marketplace=='ozon').limit(500)):
            draft=db.scalar(select(Draft).where(Draft.review_id==review.id))
            if not eligibility_reason(db,review,draft,schedule):
                selected=review.id;break
        print(json.dumps({'eligible_candidate':selected,'send_requested':send_one}),flush=True)
        if not send_one or not selected: return
        proposal=propose_publish(db,[selected])
        if len(proposal['payload']['items'])!=1 or proposal['payload'].get('excluded'):
            raise ValueError('Candidate changed; no send')
        original=httpx.AsyncClient
        async def capture(response):
            if response.url.host=='api-seller.ozon.ru' and response.url.path=='/v1/review/comment/create':
                await response.aread()
                try: data=response.json()
                except ValueError: data={}
                message=redact(str(data.get('message','')).replace(token,'[REDACTED]'))[:600]
                print(json.dumps({'http_status':response.status_code,'code':data.get('code'),'message':message},ensure_ascii=False),flush=True)
        def client(*args,**kwargs):
            kwargs['event_hooks']={'response':[capture]}
            return original(*args,**kwargs)
        httpx.AsyncClient=client
        try:
            result=await confirm_action(db,proposal['id'],actor_id=actor_id)
            print(json.dumps({'action_id':proposal['id'],'result':result['result']},ensure_ascii=False),flush=True)
        finally: httpx.AsyncClient=original


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--store',required=True);parser.add_argument('--actor',required=True)
    parser.add_argument('--send-one',action='store_true');args=parser.parse_args()
    asyncio.run(inspect(args.store,args.actor,args.send_one))
