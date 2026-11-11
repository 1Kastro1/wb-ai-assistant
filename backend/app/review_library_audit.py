"""Sequential, checkpointed audit; no imports, publication or rewriting saved replies."""
import asyncio
import time
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import select, func
from .db import Session, Job, Review, Draft, Product, Publication, StoreProfile, ReviewLibraryAuditRow, setting, set_setting, now

KIND='review_library_audit'
MAINTENANCE='review_library_maintenance'
ORDER=('yandex','wb','ozon')


def published(review):
    return bool(review.is_answered or review.status=='published')


def publication_protected(db,review):
    if review.status in ('publishing','answer_pending','pending_confirmation'):return True
    states=('in_flight','uncertain','answer_pending','mismatch')
    if not published(review):states=(*states,'published')
    return bool(db.scalar(select(Publication.id).where(Publication.review_id==review.id,
        Publication.status.in_(states)).limit(1)))


def start(db):
    """Called with the queue consumer stopped. Caller commits activation."""
    from .automation_schedule import normalize_schedule
    from .rule_change_recheck import policy_version
    from .verified_reply_library import migrate_legacy
    active=db.scalar(select(Job).where(Job.kind==KIND,Job.status.in_(('queued','running')),Job.cancel_requested==False))
    if active:return active
    schedule=normalize_schedule(setting(db,'automation_schedule',{}))
    before=dict(schedule)
    for field in ('enabled','reviews_sync_enabled','questions_sync_enabled','sync_enabled','drafts_enabled',
                  'question_drafts_enabled','publication_enabled','auto_propose'):
        schedule[field]=False
    set_setting(db,'automation_schedule',normalize_schedule(schedule))
    set_setting(db,'safe_mode',True)
    for old in db.scalars(select(Job).where(Job.status.in_(('queued','running')))):
        old.cancel_requested=True
        old.status='cancelled'
        old.result={**(old.result or {}),'stopped_for_library_audit':now()}
    stages=[]
    for market in ORDER:
        stores=list(db.scalars(select(StoreProfile).where(StoreProfile.provider==market).order_by(StoreProfile.id)))
        for store in stores:
            total,last=db.execute(select(func.count(),func.max(Review.id)).where(
                Review.wb_account_id==store.id,Review.marketplace==market)).one()
            stages.append({'account_id':store.id,'marketplace':market,'total':total,'last_id':last or '',
                'cursor':'','checked':0,'verified':0,'attention':0})
    library=migrate_legacy(db)
    job=Job(id=str(uuid4()),kind=KIND,priority=1,max_attempts=1000000,result={
        'stages':stages,'stage':0,'checked':0,'verified':0,'attention':0,
        'total':sum(s['total'] for s in stages),'account_id':stages[0]['account_id'] if stages else '',
        'policy_version':policy_version(db),'schedule_before':before,'library':library,
        'message':'Последовательная проверка: Яндекс Маркет → Wildberries → Ozon'})
    db.add(job)
    set_setting(db,MAINTENANCE,True)
    return job


def report(db):
    from .verified_reply_library import statistics
    job=db.scalar(select(Job).where(Job.kind==KIND).order_by(Job.created_at.desc()).limit(1))
    return {'maintenance':setting(db,MAINTENANCE,False),'safe_mode':setting(db,'safe_mode',False),
        'library':statistics(db),'job':{'id':job.id,'status':job.status,**(job.result or {})} if job else None}


async def inspect_review(job_id,rid,session_factory):
    from .services import assess_reply,review_verified_facts,generate_draft
    from .owner_reply_rules import matching_owner_rules
    from .owner_reply_policy import enforce_owner_rules,attach_rule_quality
    from .generation_control import exclusive_generation,ReviewSkipped,batch_context
    from .rule_execution import verification_key
    from .learned_reply_templates import load_template,remember_template
    from .rule_retry import technical_check
    # Generate only missing drafts, through the ordinary rules-aware pipeline.
    with session_factory() as db:
        review=db.get(Review,rid)
        draft=db.scalar(select(Draft).where(Draft.review_id==rid))
        if not review:return {'status':'removed'}
        if publication_protected(db,review):return {'status':'attention','issues':['Сохранён статус публикации; требуется личная сверка']}
        missing=not published(review) and not draft
        if missing:
            token=batch_context.set({'job_id':job_id,'suppress_rule_retry':True})
            try:await generate_draft(db,rid,skip_existing=True)
            except ReviewSkipped:pass
            finally:batch_context.reset(token)
    async def verify():
        with session_factory() as db:
            review=db.get(Review,rid)
            if not review:return {'status':'removed'}
            product=db.get(Product,review.product_id)
            draft=db.scalar(select(Draft).where(Draft.review_id==rid))
            was_published=published(review)
            answer=review.existing_answer if was_published else draft.text if draft else ''
            if publication_protected(db,review):return {'status':'attention','issues':['Сохранён статус публикации; требуется личная сверка']} 
            if not product or not answer.strip():return {'status':'attention','issues':['Нет доступного текста ответа или товара']}
            rules=matching_owner_rules(db,product)
            facts=review_verified_facts(db,review,product,rules)
            key=verification_key(answer,review,product,rules,facts)
            assess=lambda text:assess_reply(review,product,text,facts)
            expected=(was_published,review.status,review.existing_answer,draft.id if draft else '',draft.revision if draft else 0,answer)
            learned=load_template(db,review,product,rules,facts,assess,exact_answer=answer)
            if learned:checked,verdict=learned
            else:
                from .generation_budget import AUTO_RULE_CHECK_SECONDS
                checked,verdict=await enforce_owner_rules(answer,review,product,rules,facts,assess,
                    protected=review.risk!='NORMAL',check_only=True,allow_fast_positive=True,
                    check_seconds=AUTO_RULE_CHECK_SECONDS)
            db.rollback()
            review=db.scalar(select(Review).where(Review.id==rid).with_for_update())
            product=db.get(Product,review.product_id) if review else None
            draft=db.scalar(select(Draft).where(Draft.review_id==rid).with_for_update())
            if (not review or not product or db.get(Job,job_id).cancel_requested):return {'status':'retry','issues':['Проверка прервана']}
            current_answer=review.existing_answer if published(review) else draft.text if draft else ''
            current=(published(review),review.status,review.existing_answer,draft.id if draft else '',draft.revision if draft else 0,current_answer)
            fresh_rules=matching_owner_rules(db,product)
            fresh_facts=review_verified_facts(db,review,product,fresh_rules)
            if (publication_protected(db,review) or current!=expected or verification_key(current_answer,review,product,fresh_rules,fresh_facts)!=key):
                return {'status':'retry','issues':['Контекст изменился во время проверки']}
            if checked!=answer:
                verdict={**verdict,'passed':False,'checked':False,'issues':['Проверка предложила изменение; исходный текст сохранён']}
            verdict={**verdict,'input_key':key,'checked_at':now()}
            quality=attach_rule_quality(assess(answer),verdict)
            if not was_published and draft:
                draft.quality={**(draft.quality or {}),**quality}
                review.status='manual_review' if review.manual or not quality['passed'] else 'draft_ready'
            tid=''
            if (quality['passed'] and not quality.get('text_issues') and verdict.get('checked') is True
                    and verdict.get('passed') is True and not verdict.get('technical_error') and not verdict.get('issues')):
                proof=SimpleNamespace(text=answer,quality=quality,id=rid)
                approved=remember_template(db,review,product,rules,facts,proof)
                from .verified_reply_library import remember,text_id
                if not approved:
                    # Risk/manual replies can be retained as verified references;
                    # never confer approval on another customer automatically.
                    remember(db,key,'reference',answer,verdict,source=rid,marketplace=review.marketplace)
                tid=text_id(answer)
            db.commit()
            return {'status':'verified' if tid else 'retry' if technical_check(quality) else
                'blocked' if verdict.get('technical_error') else 'attention',
                'input_key':key,'template_id':tid,'issues':quality.get('issues',[])}
    return await exclusive_generation(rid,verify,session_factory)


def selfcheck(db,job_id,stages):
    """Exercise the real read path in each audited market without sending replies."""
    from .verified_reply_library import statistics,text_id
    from .db import VerifiedReplyText
    from .learned_reply_templates import load_template,context_key
    from .owner_reply_rules import matching_owner_rules
    from .services import assess_reply,review_verified_facts
    if any(row.id!=text_id(row.text) for row in db.scalars(select(VerifiedReplyText))):
        raise ValueError('Нарушена целостность библиотеки шаблонов')
    results=[]
    for stage in stages:
        rid=db.scalar(select(ReviewLibraryAuditRow.review_id).join(Review,Review.id==ReviewLibraryAuditRow.review_id)
            .where(ReviewLibraryAuditRow.job_id==job_id,ReviewLibraryAuditRow.status=='verified',
                Review.wb_account_id==stage['account_id'],Review.marketplace==stage['marketplace'])
            .order_by(ReviewLibraryAuditRow.review_id).limit(1))
        if not rid:
            results.append({'marketplace':stage['marketplace'],'store':stage['account_id'],'status':'no_approved_replies'})
            continue
        review=db.get(Review,rid);product=db.get(Product,review.product_id)
        draft=db.scalar(select(Draft).where(Draft.review_id==rid))
        answer=review.existing_answer if published(review) else draft.text if draft else ''
        rules=matching_owner_rules(db,product);facts=review_verified_facts(db,review,product,rules)
        if context_key(review,product,rules,facts):
            loaded=load_template(db,review,product,rules,facts,
                lambda value:assess_reply(review,product,value,facts),exact_answer=answer)
            if not loaded:raise ValueError('Проверенный ответ больше не доступен в актуальном контексте: '+stage['marketplace'])
            # A changed policy must never reuse an old approval.
            if load_template(db,review,product,[*rules,'Контроль: другое правило'],facts,
                    lambda value:{'passed':True},exact_answer=answer):
                raise ValueError('Нарушена изоляция правил шаблона')
        results.append({'marketplace':stage['marketplace'],'store':stage['account_id'],'status':'passed' if context_key(review,product,rules,facts) else 'reference_only','sample':rid})
    schedule=setting(db,'automation_schedule',{})
    if not setting(db,'safe_mode',False) or any(schedule.get(key) for key in (
            'enabled','reviews_sync_enabled','questions_sync_enabled','drafts_enabled','question_drafts_enabled','publication_enabled')):
        raise ValueError('Автоматическое обновление, генерация и отправка должны оставаться выключенными')
    return {'markets':results,'library':statistics(db),'checked_at':now(),'publication_disabled':True}


async def step(job_id,session_factory=Session,limit=40,seconds=25):
    from .rule_change_recheck import policy_version
    from .rule_retry import retry_delay,verification_failure
    started=time.monotonic()
    for iteration in range(limit):
        with session_factory() as db:
            job=db.get(Job,job_id)
            if job.cancel_requested:job.status='cancelled';db.commit();return
            state=dict(job.result or {})
            if state['policy_version']!=policy_version(db):
                job.status='failed';job.result={**state,'message':'Правила изменены. Нужен новый полный проход; старые шаблоны не одобряют новые правила.'};db.commit();return
            stages=[dict(s) for s in state['stages']];index=state['stage']
            if index>=len(stages):
                from .verified_reply_library import statistics
                checks=selfcheck(db,job_id,stages)
                state.update(selfcheck=checks,library=statistics(db),completed_at=now(),message='Все площадки проверены. Автообновление и отправка остаются выключены.')
                job.status,job.progress,job.result='completed',100,state
                # Ordinary manual actions may use the learned library again.
                # Schedules remain disabled and safe mode still blocks sending.
                set_setting(db,MAINTENANCE,False)
                db.commit();return
            stage=stages[index]
            rid=db.scalar(select(Review.id).where(Review.wb_account_id==stage['account_id'],
                Review.marketplace==stage['marketplace'],Review.id>stage['cursor'],Review.id<=stage['last_id']).order_by(Review.id).limit(1))
            if not rid:
                stage['completed_at']=now();state.update(stages=stages,stage=index+1,
                    account_id=stages[index+1]['account_id'] if index+1<len(stages) else stage['account_id'])
                job.result=state;db.commit();continue
        try:outcome=await inspect_review(job_id,rid,session_factory)
        except asyncio.CancelledError:raise
        except Exception as exc:
            failure=verification_failure(exc)
            outcome={'status':'retry' if failure['retryable'] else 'blocked','issues':[failure['reason']],
                'failure_code':failure['failure_code']}
        with session_factory() as db:
            job=db.get(Job,job_id)
            if job.cancel_requested:job.status='cancelled';db.commit();return
            state=dict(job.result or {});stages=[dict(s) for s in state['stages']];stage=stages[state['stage']]
            row=db.get(ReviewLibraryAuditRow,(job_id,rid))
            if row is None:row=ReviewLibraryAuditRow(job_id=job_id,review_id=rid);db.add(row)
            row.status=outcome['status'];row.attempts=(row.attempts or 0)+1
            row.input_key=outcome.get('input_key','');row.template_id=outcome.get('template_id','')
            row.issues=outcome.get('issues',[]);row.updated_at=now()
            if outcome['status'] in ('retry','blocked'):
                delay=retry_delay(row.attempts) if outcome['status']=='retry' else 3600
                row.retry_at=time.time()+delay
                state.update(retry_at=row.retry_at,current_review=rid,detail='; '.join(row.issues)[:500],
                    message=f"{stage['marketplace']}: проверка сохранена, ожидает {'повтора' if outcome['status']=='retry' else 'исправления сервиса'}. Следующий маркет не запущен.")
                job.status,job.result='queued',state;db.commit();return
            stage['cursor']=rid
            for target in (stage,state):
                target['checked']=target.get('checked',0)+1
                field='verified' if outcome['status']=='verified' else 'attention'
                target[field]=target.get(field,0)+1
            state.update(stages=stages,retry_at=0,current_review='',detail='',last_progress_at=now(),
                message=f"{stage['marketplace']}: проверено {stage['checked']} из {stage['total']}, подтверждено {stage['verified']}, требуют внимания {stage['attention']}")
            job.progress=min(99,round(state['checked']*100/max(1,state['total'])))
            yielding=time.monotonic()-started>=seconds or iteration==limit-1
            job.result=state;job.status='queued' if yielding else 'running';db.commit()
        if yielding:return
    # A slice containing only empty/completed stages must also remain resumable.
    with session_factory() as db:
        job=db.get(Job,job_id)
        if job.status=='running':job.status='queued';db.commit()


if __name__=='__main__':
    import json,sys
    with Session() as db:
        if '--start' in sys.argv:
            job=start(db);db.commit();print(job.id)
        print(json.dumps(report(db),ensure_ascii=False,default=str))
