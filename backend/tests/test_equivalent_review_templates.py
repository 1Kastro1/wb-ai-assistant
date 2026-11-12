import asyncio
from types import SimpleNamespace
import pytest
from sqlalchemy import select,delete
from app.db import Session,StoreProfile,Product,Review,Draft,Memory,VerifiedReplyContext,ReviewLibraryAuditRow,Job
from app.owner_reply_policy import apply_local_preferences,apply_signature,enforce_owner_rules,attach_rule_quality
from app.owner_reply_rules import matching_owner_rules
from app.services import assess_reply,review_verified_facts
from app.safety import safe_fallback_reply
from app.rule_execution import positive_template
from app.learned_reply_templates import remember_template,load_template,context_key,backfill_equivalent_templates
from app.review_library_audit import start,inspect_review,reuse_pending_templates


def seed(db,market='yandex',text='Не пахнет',rating=3):
    db.add(StoreProfile(id=market,name=market,provider=market))
    db.add(Product(id='p',name='Ароматизатор',brand='EIKOSHA',category='Ароматизаторы',marketplace=market))
    db.add_all([Memory(id='greeting',kind='REVIEW_RULE',text='Начинай с «Добрый день!».',scope='global'),
        Memory(id='signature',kind='REVIEW_RULE',text='В конце каждого ответа на отзыв добавляй подпись «С уважением ГК Юником.».',scope='global')])
    db.flush()
    source=Review(id='a-source',wb_account_id=market,wb_review_id='a-source',product_id='p',marketplace=market,
        text=text,rating=rating,risk='NORMAL',manual=False,safety_critical=False,topics=['запах'] if text=='Не пахнет' else ['другое'])
    db.add(source);db.flush();product=db.get(Product,'p');rules=matching_owner_rules(db,product)
    facts=review_verified_facts(db,source,product,rules)
    raw=('Добрый день! Спасибо за Ваш отзыв. Приносим извинения за то, что аромат не чувствуется. '
        'Понимаем Ваше разочарование покупкой. По отзыву нельзя установить причину отсутствия запаха. '
        'Для обращения по качеству товара Вы можете воспользоваться официальной поддержкой маркетплейса.')
    if text!='Не пахнет':raw=positive_template(source,rules,product=product)
    answer,_=apply_local_preferences(raw,source,rules)
    answer=apply_signature(answer,rules)
    answer,verdict=asyncio.run(enforce_owner_rules(answer,source,product,rules,facts,
        lambda value:assess_reply(source,product,value,facts)))
    quality=attach_rule_quality(assess_reply(source,product,answer,facts),verdict)
    assert quality['passed'],quality
    assert remember_template(db,source,product,rules,facts,SimpleNamespace(text=answer,quality=quality))
    target=Review(id='b-target',wb_account_id=market,wb_review_id='b-target',product_id='p',marketplace=market,
        text={'Не пахнет':'Нет запаха!','Еще не пробовал':'Пока не использовал','Все супер!':'Все отлично!'}[text],rating=rating,risk='NORMAL',
        manual=False,safety_critical=False,topics=['другое'])
    db.add(target);db.flush()
    db.add(Draft(id='draft',review_id=target.id,text='Здравствуйте. Спасибо!',original='Здравствуйте. Спасибо!',revision=4))
    job=start(db);db.flush();return source,target,product,rules,facts,answer,job.id


@pytest.mark.parametrize('market',['yandex','wb','ozon'])
def test_equivalent_complaint_uses_verified_reply_without_rechecking_or_model(monkeypatch,market):
    with Session() as db:
        source,target,product,rules,facts,answer,jid=seed(db,market);db.commit()
    def forbidden(*args,**kwargs):raise AssertionError('A proven template must skip rule checking')
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',forbidden)
    monkeypatch.setattr('app.integrations.ollama.reply_variants',forbidden)
    result=asyncio.run(inspect_review(jid,'b-target',Session))
    with Session() as db:
        draft=db.get(Draft,'draft')
        assert result['status']=='verified' and result['template_reused']
        assert draft.text==answer and draft.revision==5
        assert draft.quality['owner_rules']['verification_method']=='learned_equivalent_context'
        assert draft.quality['owner_rules']['template_source']=='a-source'
        assert draft.edits[-1]['old']=='Здравствуйте. Спасибо!'


def test_extra_clauses_questions_changed_rules_rating_facts_and_product_are_not_equivalent():
    with Session() as db:
        source,target,product,rules,facts,answer,jid=seed(db)
        assess=lambda value:assess_reply(target,product,value,facts)
        assert load_template(db,target,product,rules,facts,assess)
        for message in ('Не пахнет, но разлилось','Не, пахнет','Нет запаха?','Запах исчез за неделю','Нет запаха на морозе'):
            target.text=message;assert load_template(db,target,product,rules,facts,assess) is None
        target.text='Нет запаха'
        assert load_template(db,target,product,rules+['Новое правило'],facts,assess) is None
        assert load_template(db,target,product,rules,facts+[{'text':'other evidence'}],assess) is None
        target.rating=5;assert load_template(db,target,product,rules,facts,assess) is None
        target.rating=3
        other=Product(id='different',name=product.name,brand=product.brand,category=product.category,marketplace=product.marketplace)
        db.add(other);db.flush()
        assert load_template(db,target,other,rules,facts,assess) is None
        db.rollback()


def test_backfill_adds_groups_only_from_unchanged_exact_proofs():
    with Session() as db:
        source,target,product,rules,facts,answer,jid=seed(db)
        db.execute(delete(VerifiedReplyContext).where(VerifiedReplyContext.kind=='equivalent'));db.flush()
        assert not load_template(db,target,product,rules,facts,lambda value:{'passed':True})
        assert backfill_equivalent_templates(db)['indexed']==1
        assert load_template(db,target,product,rules,facts,lambda value:{'passed':True})
        db.execute(delete(VerifiedReplyContext).where(VerifiedReplyContext.kind=='equivalent'));db.flush()
        product.facts=[{'text':'changed evidence'}]
        assert backfill_equivalent_templates(db)['indexed']==0


def test_pending_warm_pass_skips_full_checker_and_counts_once(monkeypatch):
    with Session() as db:
        source,target,product,rules,facts,answer,jid=seed(db)
        db.add(ReviewLibraryAuditRow(job_id=jid,review_id=target.id,status='retry',attempts=2,retry_at=9999999999))
        db.commit()
    def forbidden(*args,**kwargs):raise AssertionError('Warm pass must never check rules or invoke a model')
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',forbidden)
    assert asyncio.run(reuse_pending_templates(jid))=={'reused':1}
    assert asyncio.run(reuse_pending_templates(jid))=={'reused':0}
    with Session() as db:
        state=db.get(Job,jid).result
        assert state['checked']==state['verified']==state['template_reused']==1
        assert db.get(ReviewLibraryAuditRow,(jid,'b-target')).status=='verified'


def test_warm_pass_does_not_start_checks_when_no_matching_template(monkeypatch):
    with Session() as db:
        source,target,product,rules,facts,answer,jid=seed(db)
        target.text='Запах исчез за неделю'
        db.add(ReviewLibraryAuditRow(job_id=jid,review_id=target.id,status='retry',attempts=2,retry_at=9999999999))
        db.commit()
    def forbidden(*args,**kwargs):raise AssertionError('Unmatched warm inputs must remain queued')
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',forbidden)
    assert asyncio.run(reuse_pending_templates(jid))=={'reused':0}
    with Session() as db:
        assert db.get(Draft,'draft').revision==4
        row=db.get(ReviewLibraryAuditRow,(jid,'b-target'))
        assert row.status=='retry' and row.retry_at==9999999999


@pytest.mark.parametrize('message,method',[('Все супер!','learned_positive_group'),('Еще не пробовал','learned_equivalent_context')])
def test_praise_and_untried_template_replace_old_variant_without_another_check(monkeypatch,message,method):
    with Session() as db:
        source,target,product,rules,facts,answer,jid=seed(db,text=message,rating=5);db.commit()
    def forbidden(*args,**kwargs):raise AssertionError('Existing proof must be reused')
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',forbidden)
    outcome=asyncio.run(inspect_review(jid,'b-target',Session))
    with Session() as db:
        assert outcome['template_reused'] and outcome['status']=='verified'
        assert db.get(Draft,'draft').quality['owner_rules']['verification_method']==method


def test_published_reply_is_preserved_while_verified_template_is_reused(monkeypatch):
    with Session() as db:
        source,target,product,rules,facts,answer,jid=seed(db)
        target.is_answered=True;target.existing_answer='Старый опубликованный ответ'
        db.commit()
    def forbidden(*args,**kwargs):raise AssertionError('Template already checked')
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',forbidden)
    outcome=asyncio.run(inspect_review(jid,'b-target',Session))
    with Session() as db:
        assert outcome['status']=='verified_reference' and outcome['template_reused']
        assert db.get(Review,'b-target').existing_answer=='Старый опубликованный ответ'


def test_employee_variant_is_not_overwritten_by_template_prepass(monkeypatch):
    with Session() as db:
        source,target,product,rules,facts,answer,jid=seed(db)
        db.get(Draft,'draft').edits=[{'by':'employee'}];db.commit()
    def forbidden(*args,**kwargs):raise AssertionError('Prepass may not judge or rewrite employee edits')
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',forbidden)
    assert asyncio.run(inspect_review(jid,'b-target',Session,templates_only=True))['status']=='unmatched'
    with Session() as db:assert db.get(Draft,'draft').revision==4
