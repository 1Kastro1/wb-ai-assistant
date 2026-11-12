import asyncio
import pytest
from sqlalchemy import select
from app.db import Session,StoreProfile,Product,Review,Draft,Job,ReviewLibraryAuditRow,VerifiedReplyText
from app.review_library_audit import start,step,enable_automatic_repair
from app.owner_reply_policy import fingerprint

ANSWER='Добрый день! Спасибо за отзыв. Приносим извинения за повреждение полученного товара. Если при получении заказа Вы обнаружили повреждение, Вы можете отказаться от получения. С уважением ГК Юником.'


def seed(market,*,published=False,human=False):
    with Session() as db:
        db.add(StoreProfile(id=market,name=market,provider=market))
        db.add(Product(id='p',name='Товар',marketplace=market));db.flush()
        db.add(Review(id='r',wb_account_id=market,wb_review_id='1',product_id='p',marketplace=market,
            rating=2,text='Товар пришел разбитый',is_answered=published,existing_answer=''));db.flush()
        db.add(Draft(id='d',review_id='r',text='Спасибо!',original='Спасибо!',
            edits=[{'by':'employee'}] if human else [],revision=3))
        job=start(db);db.flush();jid=job.id;enable_automatic_repair(db,jid);db.commit();return jid


async def passed(answer,review,product,rules,*args,**kwargs):
    return answer,{'matched':len(rules),'fingerprint':fingerprint(rules),'checked':True,'passed':True,'issues':[]}


@pytest.mark.parametrize('market',['yandex','wb','ozon'])
def test_only_verified_replacement_is_saved_with_revision_history(monkeypatch,market):
    monkeypatch.setattr('app.auto_review_repair.repair',lambda *a: replacement())
    async def checked(*args,**kwargs):return await passed(*args,**kwargs)
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',checked)
    jid=seed(market)
    asyncio.run(step(jid,limit=8))
    with Session() as db:
        draft=db.get(Draft,'d');assert draft.text==ANSWER and draft.revision==4
        assert draft.edits[-1]['old']=='Спасибо!' and draft.edits[-1]['by']=='automation'
        assert draft.quality['owner_rules']['checked'] and draft.quality['passed']
        assert db.get(Job,jid).status=='completed'
        assert db.get(ReviewLibraryAuditRow,(jid,'r')).status=='verified'


async def replacement(*,technical=False):
    return ANSWER,{'passed':not technical,'issues':['temporary'] if technical else [],
        'owner_rules':{'checked':not technical,'passed':not technical,'technical_error':technical,'issues':['temporary'] if technical else []},
        'automatic_repair':{'version':'test','case':'damaged'}}


def test_outage_preserves_saved_text_and_retries(monkeypatch):
    monkeypatch.setattr('app.auto_review_repair.repair',lambda *a: replacement(technical=True))
    jid=seed('yandex');asyncio.run(step(jid,limit=1))
    with Session() as db:
        assert db.get(Draft,'d').text=='Спасибо!' and db.get(Draft,'d').revision==3
        assert db.get(ReviewLibraryAuditRow,(jid,'r')).status=='retry'
        assert not db.scalar(select(VerifiedReplyText.id))


def test_employee_revision_is_never_automatically_replaced(monkeypatch):
    async def forbidden(*args):raise AssertionError('human text must be preserved')
    monkeypatch.setattr('app.auto_review_repair.repair',forbidden)
    jid=seed('yandex',human=True);asyncio.run(step(jid,limit=1))
    with Session() as db:
        assert db.get(Draft,'d').text=='Спасибо!'
        assert db.get(ReviewLibraryAuditRow,(jid,'r')).status=='attention'


def test_published_missing_text_gets_separate_checked_reference_without_remote_edit(monkeypatch):
    monkeypatch.setattr('app.auto_review_repair.repair',lambda *a: replacement())
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',passed)
    jid=seed('yandex',published=True);asyncio.run(step(jid,limit=8))
    with Session() as db:
        assert db.get(Review,'r').existing_answer=='' and db.get(Review,'r').is_answered
        assert db.get(Draft,'d').text=='Спасибо!'
        row=db.get(ReviewLibraryAuditRow,(jid,'r'));assert row.status=='verified_reference'
        assert db.get(VerifiedReplyText,row.template_id).text==ANSWER
        assert db.get(Job,jid).result['corrected_references']==1
        assert db.get(Job,jid).status=='completed'


def test_upgrade_revisits_attention_once_without_losing_successful_counts(monkeypatch):
    jid=seed('yandex',human=True);asyncio.run(step(jid,limit=1))
    with Session() as db:
        job=db.get(Job,jid);job.result={**job.result,'repair_version':'old'};job.status='queued'
        assert job.result['attention']==1
        enable_automatic_repair(db,jid);db.commit();enable_automatic_repair(db,jid);db.commit()
        assert job.result['checked']==0 and job.result['attention']==0
        assert db.get(ReviewLibraryAuditRow,(jid,'r')).status=='retry'


def test_concurrent_edit_prevents_replacement_and_old_approval(monkeypatch):
    async def edit(*args):
        with Session() as db:
            d=db.get(Draft,'d');d.text='Правка сотрудника';d.revision+=1;db.commit()
        return await replacement()
    monkeypatch.setattr('app.auto_review_repair.repair',edit)
    jid=seed('yandex');asyncio.run(step(jid,limit=1))
    with Session() as db:
        assert db.get(Draft,'d').text=='Правка сотрудника'
        assert not db.scalar(select(VerifiedReplyText.id))
        assert db.get(ReviewLibraryAuditRow,(jid,'r')).status=='retry'


def test_new_unknown_rule_is_not_waived_by_automatic_recipe(monkeypatch):
    from unittest.mock import AsyncMock
    from app.auto_review_repair import repair
    from app.services import assess_reply
    jid=seed('yandex')
    judge=AsyncMock(return_value={'compliant':False,'violations':['Не выполнено особое правило'],'revised_answer':''})
    monkeypatch.setattr('app.integrations.ollama.structured',judge)
    with Session() as db:
        review=db.get(Review,'r');product=db.get(Product,'p')
        assert asyncio.run(repair(review,product,['Особое правило: перечисли подтверждённые свойства товара.'],[],
            lambda text:assess_reply(review,product,text,[]),local_only=True)) is None
        assert not judge.called
        _,quality=asyncio.run(repair(review,product,['Особое правило: перечисли подтверждённые свойства товара.'],[],
            lambda text:assess_reply(review,product,text,[])))
        assert judge.called and not quality['passed']
        review.risk='HIGH'
        assert asyncio.run(repair(review,product,[],[],lambda text:{})) is None


def test_independently_proven_recipe_avoids_waiting_for_old_positive_prose(monkeypatch):
    from app.generation_control import FastPathUnavailable
    calls=[]
    async def old_check(*args,**kwargs):
        calls.append(kwargs.get('local_only'))
        assert kwargs.get('local_only') is True
        raise FastPathUnavailable('old wording needs semantic check')
    async def fast(*args,**kwargs):
        assert kwargs.get('local_only') is True
        _,quality=await replacement()
        return 'Добрый день! Спасибо за Ваш отзыв. С уважением ГК Юником.',quality
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',old_check)
    monkeypatch.setattr('app.auto_review_repair.repair',fast)
    jid=seed('yandex')
    with Session() as db:
        r=db.get(Review,'r');r.text='Отлично!';r.rating=5
        d=db.get(Draft,'d');d.text=d.original='Добрый день! Спасибо за отзыв. Будем рады видеть Вас снова в нашем магазине.'
        db.commit()
    asyncio.run(step(jid,limit=1))
    with Session() as db:
        assert calls==[True]
        assert db.get(Draft,'d').text=='Добрый день! Спасибо за Ваш отзыв. С уважением ГК Юником.'
        assert db.get(ReviewLibraryAuditRow,(jid,'r')).status=='verified'


def test_fully_audited_low_rating_leaves_routine_personal_queue_but_outage_does_not():
    from app.services import manual_review_condition
    from app.attention_queue import personal_condition
    jid=seed('yandex')
    with Session() as db:
        draft=db.get(Draft,'d');draft.quality={'passed':True,'automatic_audit':{'input_key':'fresh'},
            'owner_rules':{'checked':True,'passed':True}}
        db.get(Review,'r').status='draft_ready';db.flush()
        query=select(Review.id).join(Draft,Draft.review_id==Review.id)
        assert not db.scalar(query.where(manual_review_condition()))
        assert not db.scalar(query.where(personal_condition({})))
        draft.quality={**draft.quality,'owner_rules':{'checked':False,'passed':False,'technical_error':True}}
        db.flush();assert db.scalar(query.where(personal_condition({})))=='r'
