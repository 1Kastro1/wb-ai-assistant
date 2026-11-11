"""One resumable review refresh per store; independent fast and contextual lanes."""
import asyncio
import hashlib
import time
from sqlalchemy import select, func, and_, or_
from .db import Session, Job, Review, Draft, Publication, GenerationOutcome, StoreProfile, User, now, setting

VERSION = 'review-policy-2026-11-03-v2'


def protected_draft(db, review, draft, recovery):
    from .draft_audit import manually_edited
    job = db.get(Job, recovery['job_id'])
    return (not job or job.cancel_requested or job.status not in ('queued','running') or
        not review or review.wb_account_id != recovery['account_id'] or
        review.marketplace not in ('wb','ozon','yandex') or review.is_answered or
        review.status in ('publishing','published','pending_confirmation') or
        bool(draft and manually_edited(draft)) or
        bool(db.scalar(select(Publication.id).where(Publication.review_id == review.id))))


def scope(account, cutoff):
    return select(Review).where(Review.wb_account_id == account,
        Review.marketplace.in_(('wb','ozon','yandex')), Review.is_answered == False,
        ~Review.status.in_(('publishing','published','pending_confirmation')), Review.created_at <= cutoff,
        ~and_(Review.marketplace=='ozon',func.trim(Review.text)==''),
        ~select(Publication.id).where(Publication.review_id == Review.id).correlate(Review).exists())


def current_reply(db,review,draft):
    """Reuse an already verified answer only after fresh local content checks."""
    if not draft or not (draft.quality or {}).get('passed'):return False
    from .services import matching_owner_rules,review_verified_facts,assess_reply
    from .db import Product
    from .owner_reply_policy import saved_rule_quality,local_issues
    from .reply_policy import semantic_reply_issues
    verdict=(draft.quality or {}).get('owner_rules') or {}
    if verdict.get('checked') is not True or verdict.get('passed') is not True or verdict.get('technical_error'):
        return False
    product=db.get(Product,review.product_id);rules=matching_owner_rules(db,product)
    return (saved_rule_quality(draft.text,rules,verdict,review=review)['passed'] and
        not local_issues(draft.text,rules,review) and not semantic_reply_issues(review,product,draft.text) and
        assess_reply(review,product,draft.text,review_verified_facts(db,review,product,rules))['passed'])


def enqueue_all(db, actor_id):
    from .db import Memory
    actor = db.get(User, actor_id)
    if not actor or not actor.enabled or actor.role != 'owner':
        raise ValueError('Нужен действующий владелец')
    rules = [(r.id,r.text,r.scope) for r in db.scalars(select(Memory).where(
        Memory.enabled == True, Memory.kind != 'QUESTION_RULE').order_by(Memory.id))]
    jobs = []
    for store in db.scalars(select(StoreProfile).where(StoreProfile.provider.in_(('wb','ozon','yandex'))).with_for_update()):
        jid = 'regenerate-' + hashlib.sha256(repr((VERSION,store.id,rules)).encode()).hexdigest()[:24]
        job = db.get(Job,jid)
        if not job:
            cutoff = now()
            total = db.scalar(select(func.count()).select_from(scope(store.id,cutoff).subquery())) or 0
            job = Job(id=jid,kind='draft_recheck',priority=45,max_attempts=1000000,result={
                'policy_regeneration':True,'policy_version':VERSION,'account_id':store.id,
                'requested_by':actor_id,'cutoff':cutoff,'total':total,'last_served_at':'',
                'processed':0,'saved':0,'preserved':0,'errors':0,
                'message':'Обновление всех отзывов по текущим правилам'})
            db.add(job);db.flush()
            # A complete refresh covers older review batches, including selected
            # reviews, but never cancels question generation or publication.
            for old in db.scalars(select(Job).where(Job.id != jid,
                    Job.status.in_(('queued','running')),Job.cancel_requested == False,
                    Job.result['account_id'].as_string() == store.id)):
                state=old.result or {}
                if old.kind=='drafts' or old.kind=='draft_recheck' and state.get('policy_regeneration'):
                    old.cancel_requested=True
                    if old.status=='queued':old.status='cancelled'
                    old.result={**state,'superseded_by':jid,'message':'Продолжение в общем обновлении отзывов магазина'}
        jobs.append(job)
    db.commit()
    return jobs


def recount(db,job):
    counts=dict(db.execute(select(GenerationOutcome.category,func.count()).where(
        GenerationOutcome.job_id==job.id).group_by(GenerationOutcome.category)).all())
    state=dict(job.result or {})
    state.update(processed=sum(v for k,v in counts.items() if k!='deferred_fast'),saved=counts.get('generated',0),
        preserved=counts.get('preserved',0),errors=counts.get('attention',0),updated_at=now())
    job.result=state
    job.progress=min(99,int(100*state['processed']/max(1,state.get('total',0))))


async def lane(job_id,name,session_factory,limit):
    from .services import generate_draft
    from .generation_control import batch_context,GenerationBusy,ReviewSkipped,FastPathUnavailable
    from .batch_processing import fast_condition,transient
    started=time.monotonic()
    for _ in range(limit):
        with session_factory() as db:
            job=db.get(Job,job_id);state=dict(job.result or {})
            if job.cancel_requested or job.status in ('completed','cancelled') or state.get(name+'_done'):
                return
            if state.get(name+'_retry_at',0)>time.time():return
            query=scope(state['account_id'],state['cutoff'])
            if name=='fallback':
                query=select(Review).where(Review.wb_account_id==state['account_id'],select(GenerationOutcome.review_id).where(
                    GenerationOutcome.job_id==job_id, GenerationOutcome.review_id==Review.id,
                    GenerationOutcome.category=='deferred_fast').correlate(Review).exists())
            else:
                query=query.where(fast_condition() if name=='fast' else ~fast_condition())
            cursor=state.get(name+'_cursor') if name!='fallback' else None
            if cursor:
                query=query.where(or_(Review.created_at<cursor[0],and_(Review.created_at==cursor[0],Review.id<cursor[1])))
            review=db.scalar(query.order_by(Review.created_at.desc(),Review.id.desc()).limit(1))
            if not review:
                job=db.scalar(select(Job).where(Job.id==job_id).with_for_update().execution_options(populate_existing=True))
                if name!='fallback':job.result={**job.result,name+'_done':True}
                db.commit();return
            rid=review.id;position=[review.created_at,review.id]
            draft=db.scalar(select(Draft).where(Draft.review_id==rid))
            recovery={'policy_regeneration':True,'job_id':job_id,'account_id':state['account_id']}
            category=None;failure=None
            previous=db.get(GenerationOutcome,(job_id,rid))
            if not previous or previous.category=='deferred_fast':
                if protected_draft(db,review,draft,recovery):category='preserved'
                elif current_reply(db,review,draft):
                    from .learned_reply_templates import remember_template
                    from .services import matching_owner_rules,review_verified_facts
                    from .db import Product
                    product=db.get(Product,review.product_id);rules=matching_owner_rules(db,product)
                    remember_template(db,review,product,rules,review_verified_facts(db,review,product,rules),draft)
                    from .generation_control import record_usage
                    record_usage(db,job_id,rid,'reused',updated=False)
                    category='generated'
                elif draft:
                    # A policy refresh verifies saved text. It is not permission
                    # to replace an existing reply with a newly generated answer.
                    from .rule_change_recheck import enqueue_draft
                    enqueue_draft(db,draft,review,state.get('requested_by',''))
                    category='preserved'
                else:
                    token=batch_context.set({'job_id':job_id,'policy_regeneration':True})
                    try:
                        await generate_draft(db,rid,recovery=recovery,fast_only=name=='fast')
                    except FastPathUnavailable:
                        db.rollback()
                        # Defer only this review. The durable outcome survives a
                        # restart without a huge JSON list or a global rescan.
                        category='deferred_fast'
                    except GenerationBusy:
                        db.rollback();failure='busy'
                    except ReviewSkipped:
                        db.rollback();category='preserved'
                    except Exception as error:
                        db.rollback()
                        if transient(error):failure='service'
                        elif isinstance(error,ValueError):category='attention'
                        else:raise
                    finally:batch_context.reset(token)
            db.expire_all()
            job=db.scalar(select(Job).where(Job.id==job_id).with_for_update().execution_options(populate_existing=True))
            if failure:
                job.result={**job.result,name+'_retry_at':time.time()+(5 if failure=='busy' else 60),
                    'message':'Прогресс сохранён. Ожидаем освобождения сервиса; быстрые ответы продолжают готовиться.'}
                db.commit();return  # No cursor advance and no lost failed review.
            if category:
                db.merge(GenerationOutcome(job_id=job_id,review_id=rid,attention=category=='attention',category=category))
            job.result={**job.result,name+'_cursor':position,name+'_retry_at':0,'last_served_at':now(),'last_progress_at':now()}
            db.commit()
        await asyncio.sleep(0)
        if time.monotonic()-started>=30:return


async def step(job_id,session_factory=Session,limit=1000):
    with session_factory() as db:
        job=db.get(Job,job_id)
        if job.cancel_requested or job.status in ('completed','cancelled'):
            if job.cancel_requested:job.status='cancelled';db.commit()
            return
        if not setting(db,'assistant_enabled',True):
            job.status='queued';job.result={**job.result,'retry_at':time.time()+60,
                'message':'Ожидаем включения ассистента. Прогресс сохранён.'};db.commit();return
        if (job.result or {}).get('fast_disabled'):
            # Resume jobs made by the previous implementation. Committed
            # outcomes keep already processed reviews from generating twice.
            job.result={**job.result,'fast_disabled':False,'fast_done':False,
                'fast_cursor':None,'lane_version':2}
        job.status='running';db.commit()
    with session_factory() as db:
        pending=db.scalar(select(GenerationOutcome.review_id).where(
            GenerationOutcome.job_id==job_id,GenerationOutcome.category=='deferred_fast').limit(1))
    await asyncio.gather(lane(job_id,'fast',session_factory,limit),
        lane(job_id,'fallback' if pending else 'context',session_factory,limit))
    with session_factory() as db:
        job=db.scalar(select(Job).where(Job.id==job_id).with_for_update())
        recount(db,job);state=dict(job.result or {})
        if job.cancel_requested:job.status='cancelled'
        elif state.get('fast_done') and state.get('context_done') and not db.scalar(
                select(GenerationOutcome.review_id).where(GenerationOutcome.job_id==job_id,
                    GenerationOutcome.category=='deferred_fast').limit(1)):
            job.status,job.progress='completed',100
            state.update(finished_at=now(),message='Обновление завершено. Ручные правки и отправленные ответы сохранены.')
        else:
            job.status='queued'
            due=[state.get(n+'_retry_at',0) for n in ('fast','context') if not state.get(n+'_done')]
            if db.scalar(select(GenerationOutcome.review_id).where(GenerationOutcome.job_id==job_id,
                    GenerationOutcome.category=='deferred_fast').limit(1)):
                due.append(state.get('fallback_retry_at',0))
            state.update(retry_at=min(due) if due else 0,last_served_at=now(),
                message=f"Обработано: {state['processed']} из {state['total']}. Сохранено: {state['saved']}; защищено: {state['preserved']}; требуют внимания: {state['errors']}.")
        job.result=state;db.commit()


def runtime(db):
    rows=db.scalars(select(Job).where(Job.kind=='draft_recheck',Job.result['policy_regeneration'].as_boolean()==True).order_by(Job.created_at.desc()))
    seen=set();result=[]
    for job in rows:
        state=job.result or {};account=state.get('account_id')
        if account in seen:continue
        seen.add(account);store=db.get(StoreProfile,account)
        result.append({'id':job.id,'store':store.name if store else account,'status':job.status,'progress':job.progress,
            **{k:state.get(k) for k in ('processed','total','saved','preserved','errors','message','updated_at')}})
    return {'version':VERSION,'stores':result}
