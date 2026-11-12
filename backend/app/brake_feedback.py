"""Audited, no-technical-claims replies for two owner-authorized brake scenarios."""
import re

OLD_RULE = ('Рекомендации для отзывов — Тормозные колодки и диски. При скрипе, вибрации или слабом торможении объясняй, что по отзыву нельзя установить причину и нужна проверка деталей, установки и состояния системы в сервисе. Не утверждай, что любой скрип нормален или обязательно исчезнет после притирки. При ухудшении торможения не рекомендуй продолжать эксплуатацию. Применяй только при соответствующей жалобе и категории товара; уже сообщённые сведения повторно не запрашивай.')
RULE = OLD_RULE + '\n\n' + ('Отрицания и подтверждённый покупателем опыт: «без биения и трещин», «трещин нет», «не скрипит» не являются жалобой на эти неисправности. Если покупатель сообщил о пробеге и отсутствии биения и трещин, поблагодари и отметь именно его положительные впечатления от работы дисков. Не гарантируй ресурс, безопасность и отсутствие брака; не добавляй притирку или диагностику к похвале без реальной жалобы.\n\n'
    'Первые впечатления до проверки: если диски ещё не распакованы, не установлены или не проверены, благодари только за первые впечатления и внешний вид. Не приписывай покупателю успешную установку, хорошее торможение, проверенное качество или довольство использованием. При отличии оформления коробки от изображения в карточке признай именно это отличие, кратко извинись за неудобство и предложи сверить маркировку и артикул товара с заказом. Не называй это доказательством подделки, несовместимости или повреждения диска. Не подтверждай правильность масляного покрытия без проверенных сведений изготовителя. Не запрашивай фото, видео или уже сообщённые данные; не предлагай компенсацию и возврат по своей инициативе. Реальные неисправности и вопросы разбирай по отдельным действующим правилам. Приветствие, подпись и стиль — по общим правилам магазина.')
UPGRADE = 'brake-feedback-policy-2026-11-12-v1'


def case(review, product):
    from .reply_policy import analyze_customer_message,complaint_text,packaging_artwork_mismatch
    from .product_guidance import untested_review
    from .vehicle_identifiers import vehicle_identifiers
    if (not review or not product or getattr(review,'rating',0) not in (4,5) or getattr(review,'risk','NORMAL')!='NORMAL'
        or getattr(review,'manual',False) or getattr(review,'safety_critical',False)
        or (getattr(review,'marketplace','') or '').endswith('_question')):return None
    name=((getattr(product,'name','') or '')+' '+(getattr(product,'category','') or '')).lower().replace('ё','е')
    if not re.search(r'диск\w*\s+тормозн|тормозн\w*\s+диск',name):return None
    text=' '.join((review.text or '').lower().replace('ё','е').split())
    analysis=analyze_customer_message(review.text,review.rating)
    if analysis['is_question'] or analysis['buyer_asks_about_money'] or vehicle_identifiers(text):return None
    # Explicit adverse circumstances override praise and apparent packaging-only cases.
    faults=complaint_text(text)
    if re.search(r'трещин|тресн|треснул|биени|скрип|вибрац|крив|перегрев|заклини|не тормоз|плохо тормоз|хуже тормоз|разбит|поврежд|сломан|ржав|травм|авари|\bдтп\b|поддел|не оригинал|нет маркиров',faults):return None
    key=body=None
    if (not analysis['complaints'] and not untested_review(text)
        and re.search(r'пробег\s+\d+|проехал\w*\s+\d+|\d+\s*(?:тыс\.?\s*)?км\b',text)
        and re.search(r'без\s+биени\w*\s+(?:и|,)\s*трещин\w*|биени\w*\s+нет[^.!?]{0,25}трещин\w*\s+нет',text)):
        key='brake_positive_mileage'
        body='Спасибо за подробный отзыв. Рады, что Вы отметили работу дисков без биения и трещин после пробега. Желаем Вам хорошего дня.'
    elif (untested_review(text) and set(analysis['complaints']) <= {'DESCRIPTION_MISMATCH','PACKAGING_ISSUE'}
        and re.search(r'не\s+(?:провер\w*|установ\w*|распакова\w*)',text)
        and packaging_artwork_mismatch(text)
        and not re.search(r'помят|порван|вскрыт|не хватает|не подош|не тот товар|другая модель|другой артикул|размер[^.!?]{0,30}не совп|(?:сам диск|сам товар)[^.!?]{0,30}не соответств',faults)):
        key='brake_untried_packaging'
        body='Спасибо за отзыв и первые впечатления о внешнем виде дисков. Извините за неудобство из-за отличия оформления коробки от изображения в карточке. Пожалуйста, сверьте маркировку и артикул товара с заказом.'
    return {'key':key,'answer':'Добрый день! '+body,'sources':[]} if key else None


def template(review,product,rules):
    from .rule_execution import profile,select_rules
    from .owner_reply_policy import constraints,apply_local_preferences,apply_signature,local_issues
    from .safety import validate_reply
    value=case(review,product)
    if not value or RULE not in rules:return None
    selected=select_rules(rules,review,product)[0]
    if any(constraints([rule])['semantic'] and not profile(rule).get('local_brake_feedback') for rule in selected):return None
    answer,_=apply_local_preferences(value['answer'],review,rules)
    try:answer=validate_reply(apply_signature(answer,rules))
    except ValueError:return None
    return answer if not local_issues(answer,rules,review) else None


def upgrade_policy(db,job_id):
    """Worker-stopped transaction: reopen only rows affected by this category rule."""
    from sqlalchemy import select,func
    from .db import Memory,Job,Review,Product,ReviewLibraryAuditRow,setting,set_setting,now
    from .owner_reply_rules import matching_owner_rules
    from .rule_change_recheck import policy_version
    if setting(db,UPGRADE,None):return {'already_applied':True}
    job=db.scalar(select(Job).where(Job.id==job_id).with_for_update())
    if not job or job.cancel_requested or job.status not in ('queued','running'):raise ValueError('Active audit required')
    state=dict(job.result or {})
    if state['policy_version']!=policy_version(db):raise ValueError('Unexpected policy change before upgrade')
    rules=list(db.scalars(select(Memory).where(Memory.kind=='REVIEW_RULE',Memory.enabled==True,
        Memory.scope=='category:Тормозные колодки и диски',Memory.text==OLD_RULE).with_for_update()))
    if len(rules)!=1:raise ValueError('Expected one unchanged category rule')
    rule=rules[0];rule.text=RULE;db.flush()
    stages=[dict(s) for s in state['stages']];reopened=[];first=state['stage']
    for row,review,product in db.execute(select(ReviewLibraryAuditRow,Review,Product)
            .join(Review,Review.id==ReviewLibraryAuditRow.review_id).join(Product,Product.id==Review.product_id)
            .where(ReviewLibraryAuditRow.job_id==job_id)):
        if RULE not in matching_owner_rules(db,product):continue
        index=next(i for i,s in enumerate(stages) if s['account_id']==review.wb_account_id and s['marketplace']==review.marketplace)
        stage=stages[index];first=min(first,index)
        if row.status in ('verified','verified_reference','attention'):
            for target in (stage,state):
                target['checked']-=1
                target['attention' if row.status=='attention' else 'verified']-=1
                if row.status=='verified_reference':target['corrected_references']=max(0,target.get('corrected_references',0)-1)
        row.status='retry';row.retry_at=0;row.issues=[];row.updated_at=now();reopened.append(row.review_id)
    for stage in stages[first:]:stage.pop('completed_at',None)
    db.flush()
    deferred=db.scalar(select(func.count()).select_from(ReviewLibraryAuditRow).where(
        ReviewLibraryAuditRow.job_id==job_id,ReviewLibraryAuditRow.status.in_(('retry','blocked')))) or 0
    state.update(stages=stages,stage=first,account_id=stages[first]['account_id'] if first<len(stages) else state['account_id'],policy_version=policy_version(db),
        current_review='',detail='',retry_at=0,deferred=deferred,message='Проверка отзывов о тормозных дисках по уточнённым правилам')
    job.result=state;job.status='queued'
    job.progress=min(99,round(state['checked']*100/max(1,state['total'])))
    meta=dict(setting(db,'memory-meta:'+rule.id,{}) or {})
    meta.update(updated_at=now(),updated_by_name='Автоматизация',reason=UPGRADE)
    set_setting(db,'memory-meta:'+rule.id,meta)
    set_setting(db,UPGRADE,{'at':now(),'rule_id':rule.id,'previous_text':OLD_RULE,'text':RULE,'reopened':reopened})
    return {'rule_id':rule.id,'reopened':len(reopened),'previous_attention':sum(s.get('attention',0) for s in stages)}
