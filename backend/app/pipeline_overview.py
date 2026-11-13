"""Store aggregates and durable campaign progress; no buyer bodies or prompts."""
from sqlalchemy import select, func, case, and_
from .db import Review, Draft, Publication, Job, GenerationOutcome, setting
from .attention_queue import personal_condition, automatic_publication
from .automation_schedule import normalize_schedule
from .read_cache import cached_read
from datetime import datetime, timezone, timedelta


def overview(db, stores):
    ids=tuple(sorted(stores))
    return cached_read(db,('pipeline-overview',*ids),lambda: _overview(db,stores),seconds=15)


def _overview(db,stores):
    schedule=normalize_schedule(setting(db,'automation_schedule',{}))
    personal=personal_condition(schedule)
    unanswered=Review.is_answered == False
    free=Publication.id.is_(None)
    ready=and_(unanswered,free,~personal,Draft.quality_passed == True,
        Draft.quality_pending == False,Draft.quality_technical == False,Draft.quality_attention == False)
    attention=and_(unanswered,personal)
    technical=and_(unanswered,~personal,Draft.quality_technical == True)
    pending=and_(unanswered,~personal,Draft.quality_pending == True,Draft.quality_technical == False)
    negative=Review.rating.between(1,3)
    sent=Publication.status == 'published'
    moscow=timezone(timedelta(hours=3))
    current=datetime.now(moscow)
    midnight=current.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc).isoformat()
    tomorrow=(current.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)).astimezone(timezone.utc).isoformat()
    flags={'unanswered':unanswered,'ready':ready,'personal':attention,'technical':technical,'pending':pending,
        'published':sent,'published_today':and_(sent,Publication.confirmed_at>=midnight,Publication.confirmed_at<tomorrow),
        'awaiting_moderation':Publication.status=='answer_pending',
        'technical_stopped':and_(technical,Draft.quality['owner_rules']['retryable'].as_boolean()==False),
        'negative_ready':and_(negative,ready),'negative_personal':and_(negative,attention),
        'negative_published':and_(negative,sent),'safety':and_(attention,Review.risk!='NORMAL'),
        'content':and_(attention,Draft.quality_attention==True),
        'reconciliation':and_(unanswered,Publication.status.in_(('uncertain','mismatch')))}
    columns=[func.sum(case((condition,1),else_=0)).label(key) for key,condition in flags.items()]
    rows=db.execute(select(Review.wb_account_id,*columns).outerjoin(Draft,Draft.review_id==Review.id)
        .outerjoin(Publication,Publication.review_id==Review.id).where(Review.wb_account_id.in_(stores),
            Review.marketplace.in_(('wb','yandex','ozon')),
            ~and_(Review.marketplace=='ozon',func.trim(Review.text)==''))
        .group_by(Review.wb_account_id)).mappings().all()
    counts={r['wb_account_id']:{k:int(r[k] or 0) for k in flags} for r in rows}
    movements=dict(db.execute(select(Review.wb_account_id,func.max(GenerationOutcome.created_at))
        .join(Review,Review.id==GenerationOutcome.review_id).where(Review.wb_account_id.in_(stores),
            GenerationOutcome.created_at!='').group_by(Review.wb_account_id)).all())
    latest={};stages={}
    from sqlalchemy import or_
    groups={'sync':Job.kind.in_(('products','reviews','questions')),
        'publication':Job.kind.in_(('scheduled_publication','wb_publish_send','wb_publish_prepare','publication_reconcile')),
        'generation':or_(Job.kind.in_(('drafts','single_draft','question_drafts','single_question_draft')),
            and_(Job.kind=='draft_recheck',Job.result['policy_regeneration'].as_boolean()==True)),
        'verification':or_(Job.kind=='draft_audit',and_(Job.kind=='draft_recheck',
            func.coalesce(Job.result['policy_regeneration'].as_boolean(),False)==False))}
    for sid in stores:
        stages[sid]={}
        for stage,condition in groups.items():
            completed=db.scalar(select(Job).where(Job.result['account_id'].as_string()==sid,
                Job.status=='completed',Job.cancel_requested==False,condition)
                .order_by(func.coalesce(Job.result['finished_at'].as_string(),Job.created_at).desc()).limit(1))
            if completed:
                state=completed.result or {}
                stages[sid][stage]={'status':'completed','finished_at':state.get('finished_at') or state.get('updated_at') or completed.created_at}
    for sid in stores:
        job=db.scalar(select(Job).where(Job.result['account_id'].as_string()==sid,
            Job.result['policy_regeneration'].as_boolean()==True,Job.cancel_requested==False)
            .order_by(Job.created_at.desc(),Job.id).limit(1))
        if not job:continue
        state=job.result or {};sid=state.get('account_id')
        if sid in latest:continue
        processed=int(state.get('processed',0));total=int(state.get('total',0))
        latest[sid]={'id':job.id,'status':job.status,'processed':processed,'total':total,
            'remaining':0 if job.status=='completed' else max(0,total-processed),
            'saved':state.get('saved',0),'preserved':state.get('preserved',0),'attention':state.get('errors',0),
            'usage':state.get('usage',{}),'last_progress_at':state.get('last_progress_at'),
            'started_at':job.created_at,'finished_at':state.get('finished_at')}
    result=[]
    from .pipeline_metrics import delivery_latency
    for sid,store in stores.items():
        result.append({'id':sid,'provider':store.provider,'name':store.name,
            'counts':counts.get(sid,{k:0 for k in flags}),'refresh':latest.get(sid),
            'last_progress_at':movements.get(sid),
            'delivery_latency':delivery_latency(db,sid),
            'completed_stages':stages.get(sid,{}),
            'enabled':{'sync':bool(schedule['enabled'] and schedule.get('reviews_sync_enabled')),
                'generation':bool(schedule['enabled'] and schedule.get('drafts_enabled')),
                'verification':bool(setting(db,'assistant_enabled',True)),
                'publication':automatic_publication(schedule,store.provider)},
            'auto_publication':automatic_publication(schedule,store.provider),
            'auto_negative':automatic_publication(schedule,store.provider,rating=1)})
    return result
