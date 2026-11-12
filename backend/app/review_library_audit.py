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
    from .auto_review_repair import VERSION as REPAIR_VERSION
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
        'automated_repair':True,'repair_version':REPAIR_VERSION,
        'message':'Последовательная проверка: Яндекс Маркет → Wildberries → Ozon'})
    db.add(job)
    set_setting(db,MAINTENANCE,True)
    return job


def report(db):
    from .verified_reply_library import statistics
    job=db.scalar(select(Job).where(Job.kind==KIND).order_by(Job.created_at.desc()).limit(1))
    return {'maintenance':setting(db,MAINTENANCE,False),'safe_mode':setting(db,'safe_mode',False),
        'library':statistics(db),'job':{'id':job.id,'status':job.status,**(job.result or {})} if job else None}


def enable_automatic_repair(db,job_id):
    """One-time upgrade with worker stopped; retain verified rows and counters."""
    from .auto_review_repair import VERSION
    job=db.get(Job,job_id)
    if not job or job.cancel_requested or job.status not in ('queued','running'):
        raise ValueError('Нет активной проверки для продолжения')
    state=dict(job.result or {})
    if state.get('repair_version')==VERSION:return
    stages=[dict(stage) for stage in state['stages']]
    for stage in stages:
        rows=list(db.scalars(select(ReviewLibraryAuditRow).join(Review,Review.id==ReviewLibraryAuditRow.review_id)
            .where(ReviewLibraryAuditRow.job_id==job_id,ReviewLibraryAuditRow.status=='attention',
                Review.wb_account_id==stage['account_id'],Review.marketplace==stage['marketplace'])))
        for row in rows:row.status='retry';row.retry_at=0
        for target in (stage,state):
            target['checked']=target.get('checked',0)-len(rows)
            target['attention']=target.get('attention',0)-len(rows)
    state.update(stages=stages,automated_repair=True,repair_version=VERSION,retry_at=0,
        message='Исправление замечаний и повторная проверка по действующим правилам')
    job.result=state;job.status='queued'


async def inspect_review(job_id,rid,session_factory):
    from .services import assess_reply,review_verified_facts,generate_draft
    from .owner_reply_rules import matching_owner_rules
    from .owner_reply_policy import enforce_owner_rules,attach_rule_quality,local_issues,fingerprint
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
            automatic=bool((db.get(Job,job_id).result or {}).get('automated_repair'))
            if not product or (not answer.strip() and not (automatic and was_published)):
                return {'status':'attention','issues':['Нет доступного текста ответа или товара']}
            rules=matching_owner_rules(db,product)
            facts=review_verified_facts(db,review,product,rules)
            key=verification_key(answer,review,product,rules,facts)
            assess=lambda text:assess_reply(review,product,text,facts)
            expected=(was_published,review.status,review.existing_answer,draft.id if draft else '',draft.revision if draft else 0,answer)
            local_quality=assess(answer)
            defects=list(dict.fromkeys([*((local_quality.get('issues') or []) if not local_quality.get('passed') else []),*local_issues(answer,rules,review)]))
            learned=None if defects else load_template(db,review,product,rules,facts,assess,exact_answer=answer)
            if defects:
                # A known local failure is sufficient to reject a saved reply.
                # Never spend a model timeout proving a reply already ineligible.
                checked=answer
                verdict={'checked':True,'passed':False,'technical_error':False,
                    'fingerprint':fingerprint(rules),'matched':len(rules),'issues':defects,
                    'verification_method':'local_rejection','verification_seconds':0}
            elif learned:checked,verdict=learned
            else:
                from .generation_budget import AUTO_RULE_CHECK_SECONDS
                checked,verdict=await enforce_owner_rules(answer,review,product,rules,facts,assess,
                    protected=review.risk!='NORMAL',check_only=True,allow_fast_positive=True,
                    check_seconds=AUTO_RULE_CHECK_SECONDS)
            replacement=None
            from .draft_audit import manually_edited
            if (automatic and not verdict.get('technical_error')
                and (not answer.strip() or not verdict.get('passed') or not local_quality.get('passed'))
                and (was_published or (draft and not manually_edited(draft)))):
                from .auto_review_repair import repair
                repaired=await repair(review,product,rules,facts,assess)
                if repaired:
                    value,repaired_quality=repaired
                    proof=repaired_quality.get('owner_rules') or {}
                    if (repaired_quality.get('passed') and proof.get('checked') is True
                        and proof.get('passed') is True and not proof.get('technical_error')
                        and not proof.get('issues')):
                        replacement=(value,repaired_quality)
                    elif technical_check(repaired_quality):
                        return {'status':'retry','issues':repaired_quality.get('issues',[])}
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
            if replacement:
                candidate,repaired_quality=replacement
                if not was_published:
                    if manually_edited(draft):return {'status':'retry','issues':['Правка сотрудника сохранена']}
                    # Revision history remains recoverable; commit only a fully
                    # checked replacement after the original input lock check.
                    draft.edits=[*(draft.edits or []),{'old':answer,'new':candidate,'at':now(),
                        'by':'automation','reason':'automatic_rules_repair'}]
                    draft.text=candidate;draft.revision+=1
                    draft.model='automatic-rules-repair-1.0'
                answer=candidate
                verdict=repaired_quality['owner_rules']
                key=verification_key(answer,review,product,fresh_rules,fresh_facts)
            if checked!=answer and not replacement:
                verdict={**verdict,'passed':False,'checked':False,'issues':['Проверка предложила изменение; исходный текст сохранён']}
            verdict={**verdict,'input_key':key,'checked_at':now()}
            quality=attach_rule_quality(assess(answer),verdict)
            if automatic and quality.get('passed') and verdict.get('checked') and not verdict.get('technical_error'):
                quality['automatic_audit']={'checked_at':now(),'input_key':key}
            if replacement:
                quality['automatic_repair']={**replacement[1]['automatic_repair'],
                    'previous_issues':defects,'historical_reference':was_published}
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
            return {'status':('verified_reference' if was_published and replacement else 'verified') if tid else 'retry' if technical_check(quality) else
                'blocked' if verdict.get('technical_error') else 'attention',
                'input_key':key,'template_id':tid,'issues':quality.get('issues',[]),
                'auto_repaired':bool(replacement and not was_published)}
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
        audit_row=db.scalar(select(ReviewLibraryAuditRow).join(Review,Review.id==ReviewLibraryAuditRow.review_id)
            .where(ReviewLibraryAuditRow.job_id==job_id,ReviewLibraryAuditRow.status.in_(('verified','verified_reference')),
                Review.wb_account_id==stage['account_id'],Review.marketplace==stage['marketplace'])
            .order_by(ReviewLibraryAuditRow.review_id).limit(1))
        if not audit_row:
            results.append({'marketplace':stage['marketplace'],'store':stage['account_id'],'status':'no_approved_replies'})
            continue
        rid=audit_row.review_id
        review=db.get(Review,rid);product=db.get(Product,review.product_id)
        draft=db.scalar(select(Draft).where(Draft.review_id==rid))
        answer=review.existing_answer if published(review) else draft.text if draft else ''
        if audit_row.status=='verified_reference':
            from .db import VerifiedReplyContext
            from .verified_reply_library import render
            context=db.scalar(select(VerifiedReplyContext).where(VerifiedReplyContext.template_id==audit_row.template_id,
                VerifiedReplyContext.source_review_id==rid).order_by(VerifiedReplyContext.verified_at.desc()).limit(1))
            if not context:raise ValueError('Нет доказательства проверки исправленного образца')
            answer=render(db.get(VerifiedReplyText,audit_row.template_id).text,context.presentation)
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
    from .generation_control import GenerationBusy
    started=time.monotonic();retried=False
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
                set_setting(db,MAINTENANCE,False)
                db.commit();return
            stage=stages[index]
            pending=select(ReviewLibraryAuditRow).join(Review,Review.id==ReviewLibraryAuditRow.review_id).where(
                ReviewLibraryAuditRow.job_id==job_id,ReviewLibraryAuditRow.status.in_(('retry','blocked')),
                Review.wb_account_id==stage['account_id'],Review.marketplace==stage['marketplace'])
            # Persist lane fairness across slices: a long retry must not use
            # every slice while new reviews in the same market remain.
            recorded=select(ReviewLibraryAuditRow.review_id).where(ReviewLibraryAuditRow.job_id==job_id,
                ReviewLibraryAuditRow.review_id==Review.id).exists()
            new_rid=db.scalar(select(Review.id).where(
                Review.wb_account_id==stage['account_id'],Review.marketplace==stage['marketplace'],
                Review.id>stage['cursor'],Review.id<=stage['last_id'],~recorded).order_by(Review.id).limit(1))
            due=db.scalar(pending.where(ReviewLibraryAuditRow.retry_at<=time.time())
                .order_by(ReviewLibraryAuditRow.retry_at,ReviewLibraryAuditRow.review_id).limit(1)) if (
                    not retried and (state.get('last_lane')!='retry' or not new_rid)) else None
            from_retry=due is not None
            rid=due.review_id if due else new_rid
            if not rid:
                remaining=db.scalar(select(func.count()).select_from(pending.subquery())) or 0
                if remaining:
                    earliest=db.scalar(pending.with_only_columns(func.min(ReviewLibraryAuditRow.retry_at)))
                    state.update(deferred=remaining,retry_at=max(time.time()+1,earliest or time.time()+60),
                        message=f"{stage['marketplace']}: проверено {stage['checked']} из {stage['total']}; ожидают повтора {remaining}. Следующий маркет не запущен.")
                    job.status,job.result='queued',state;db.commit();return
                stage['completed_at']=now();state.update(stages=stages,stage=index+1,deferred=0,retry_at=0,
                    account_id=stages[index+1]['account_id'] if index+1<len(stages) else stage['account_id'])
                job.result=state;db.commit();continue
        if from_retry:retried=True
        try:outcome=await inspect_review(job_id,rid,session_factory)
        except asyncio.CancelledError:raise
        except GenerationBusy:
            outcome={'status':'retry','failure_code':'generation_busy',
                'issues':['Ответ ещё готовится; проверка повторится после освобождения записи']}
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
            final_before=row.status not in (None,'retry','blocked')
            row.status=outcome['status'];row.attempts=(row.attempts or 0)+1
            row.input_key=outcome.get('input_key','');row.template_id=outcome.get('template_id','')
            row.issues=outcome.get('issues',[]);row.updated_at=now()
            if not from_retry:stage['cursor']=rid
            if outcome['status'] in ('retry','blocked'):
                delay=retry_delay(row.attempts) if outcome['status']=='retry' else 3600
                row.retry_at=time.time()+delay
                state.update(current_review=rid,detail='; '.join(row.issues)[:500])
            else:
                row.retry_at=0
                if not final_before:
                    for target in (stage,state):
                        target['checked']=target.get('checked',0)+1
                        field='verified' if outcome['status'] in ('verified','verified_reference') else 'attention'
                        target[field]=target.get(field,0)+1
                        if outcome['status']=='verified_reference':target['corrected_references']=target.get('corrected_references',0)+1
                        if outcome.get('auto_repaired'):target['auto_repaired']=target.get('auto_repaired',0)+1
                state.update(current_review='',detail='')
            db.flush()
            deferred=db.scalar(select(func.count()).select_from(ReviewLibraryAuditRow).where(
                ReviewLibraryAuditRow.job_id==job_id,ReviewLibraryAuditRow.status.in_(('retry','blocked')))) or 0
            state.update(stages=stages,retry_at=0,deferred=deferred,last_lane='retry' if from_retry else 'new',last_progress_at=now(),
                message=f"{stage['marketplace']}: проверено {stage['checked']} из {stage['total']}, подтверждено {stage['verified']}, требуют внимания {stage['attention']}, ожидают повтора {deferred}")
            job.progress=min(99,round(state['checked']*100/max(1,state['total'])))
            yielding=time.monotonic()-started>=seconds or iteration==limit-1
            job.result=state;job.status='queued' if yielding else 'running';db.commit()
        if yielding:return
    with session_factory() as db:
        job=db.get(Job,job_id)
        if job.status=='running':job.status='queued';db.commit()


if __name__=='__main__':
    import json,sys
    with Session() as db:
        if '--start' in sys.argv:
            job=start(db);db.commit();print(job.id)
        print(json.dumps(report(db),ensure_ascii=False,default=str))
