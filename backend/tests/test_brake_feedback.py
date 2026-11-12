import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from app.db import Session,StoreProfile,Product,Review,Draft,Memory,Job,ReviewLibraryAuditRow,setting
from app.brake_feedback import case,template,OLD_RULE,RULE,UPGRADE,upgrade_policy
from app.reply_policy import analyze_customer_message
from app.owner_reply_rules import matching_owner_rules
from app.owner_reply_policy import enforce_owner_rules,attach_rule_quality
from app.review_library_audit import start,inspect_review
from app.review_rule_pack143 import PACK
from app.services import assess_reply,review_verified_facts
from app.rule_change_recheck import policy_version

MILEAGE='Качество исполнения. Недостатков нет. Диски отличные. Пробег 14000, все четко без биения и трещин, хотя приходилось резко тормозить и заезжать в лужу.'
PACKAGING='На вид - отличные. Упаковка (коробка) не соответствует фото в карточке, нет модели в правом верхнем углу. Пока не распаковал и не посмотрел маркировку, не поверил. Диски в масле. Выглядят качественно.'


def seed(db,market='yandex',*,old=False):
    style=json.loads((Path(__file__).parent/'fixtures/deduplicated_style_rules.json').read_text(encoding='utf-8'))['review']
    for i,r in enumerate(PACK['rules']):
        text=style if r['text'].startswith('Стиль и формат ответа на отзыв.') else r['text']
        if text==OLD_RULE and not old:text=RULE
        db.add(Memory(id='rule'+str(i),text=text,scope=r['scope'],kind='REVIEW_RULE'))
    db.add(StoreProfile(id=market,name=market,provider=market))
    db.add(Product(id='disc',name='Диск тормозной передний',brand='ADVICS',category='Тормозные диски',marketplace=market))
    db.add(Product(id='other',name='Ароматизатор',brand='EIKOSHA',category='Ароматизаторы',marketplace=market));db.flush()
    for rid,text in [('mileage',MILEAGE),('packaging',PACKAGING),('unaffected','Все супер!')]:
        db.add(Review(id=rid,wb_review_id=rid,wb_account_id=market,marketplace=market,product_id='other' if rid=='unaffected' else 'disc',
            rating=5,text=text,risk='NORMAL',manual=False,safety_critical=False,is_answered=rid=='mileage',existing_answer=''))
        db.flush()
        if rid!='mileage':db.add(Draft(id='draft-'+rid,review_id=rid,text='Добрый день! Рады, что Вы довольны работой товара.\nС уважением ГК Юником.',original='old',revision=2))
    db.flush();job=start(db);db.flush();return job.id


@pytest.mark.parametrize('market',['yandex','wb','ozon'])
def test_both_cases_are_repaired_and_verified_under_actual_whole_policies_without_model(monkeypatch,market):
    with Session() as db:jid=seed(db,market);db.commit()
    async def forbidden(*a,**kw):raise AssertionError('Audited complete policies must not call a model')
    monkeypatch.setattr('app.integrations.ollama.structured',forbidden)
    monkeypatch.setattr('app.integrations.ollama.reply_variants',forbidden)
    with Session() as db:
        r,p=db.get(Review,'packaging'),db.get(Product,'disc');rules=matching_owner_rules(db,p)
        answer=template(r,p,rules);facts=review_verified_facts(db,r,p,rules)
        assert answer
        assert assess_reply(r,p,answer,facts)['passed'],assess_reply(r,p,answer,facts)
        value,proof=asyncio.run(enforce_owner_rules(answer,r,p,rules,facts,lambda v:assess_reply(r,p,v,facts),local_only=True))
        assert proof['passed'],proof
    for rid in ('mileage','packaging'):
        outcome=asyncio.run(inspect_review(jid,rid,Session))
        assert outcome['status']==('verified_reference' if rid=='mileage' else 'verified'),outcome
    with Session() as db:
        assert db.get(Review,'mileage').existing_answer==''
        draft=db.get(Draft,'draft-packaging')
        assert draft.quality['passed'] and draft.quality['owner_rules']['verification_method']=='local_brake_feedback'
        assert draft.revision==3 and draft.edits[-1]['old']
        assert 'маркировку и артикул' in draft.text and 'Рады, что Вы довольны работой' not in draft.text


@pytest.mark.parametrize('text',[
    'Диски хорошие, без биения и трещин.',
    'Нет биения и трещин, диски отличные.',
    'Биения нет, трещин нет. Все отлично.',
])
def test_absence_lists_do_not_invent_damage(text):
    assert 'DAMAGED_ITEM' not in analyze_customer_message(text,5)['complaints']


@pytest.mark.parametrize('text',[
    'Без биения, но появились трещины.',
    'Без биения и трещин не обошлось.',
    'Не без трещин.',
])
def test_actual_damage_and_double_negation_are_preserved(text):
    assert 'DAMAGED_ITEM' in analyze_customer_message(text,5)['complaints']


def test_unknown_rule_extra_claim_safety_question_and_changed_product_do_not_inherit_approval():
    with Session() as db:
        seed(db);r=db.get(Review,'packaging');p=db.get(Product,'disc');rules=matching_owner_rules(db,p)
        assert template(r,p,rules)
        assert template(r,p,rules+['Обещай замену завтра.']) is None
        answer=template(r,p,rules)
        facts=review_verified_facts(db,r,p,rules)
        from app.generation_control import FastPathUnavailable
        with pytest.raises(FastPathUnavailable):
            asyncio.run(enforce_owner_rules(answer+' Товар абсолютно безопасен.',r,p,rules,facts,
                lambda v:assess_reply(r,p,v,facts),local_only=True))
        for suffix in (' Диск треснул.',' Как установить?',' Верните деньги.',' Диски кривые.',' Это подделка.'):
            r.text=PACKAGING+suffix;assert template(r,p,rules) is None
        r.text=PACKAGING;r.safety_critical=True;assert template(r,p,rules) is None
        r.safety_critical=False;r.manual=True;assert template(r,p,rules) is None
        r.manual=False;r.rating=2;assert template(r,p,rules) is None
        r.rating=5;assert template(r,db.get(Product,'other'),rules) is None


def test_policy_upgrade_reopens_only_affected_rows_preserves_checkpoint_and_is_idempotent():
    with Session() as db:
        jid=seed(db,old=True);job=db.get(Job,jid);state=dict(job.result);stages=[dict(s) for s in state['stages']]
        stage=stages[0];stage.update(checked=3,verified=1,attention=2,cursor='unaffected')
        state.update(stages=stages,checked=3,verified=1,attention=2)
        job.result=state
        for rid in ('mileage','packaging','unaffected'):
            db.add(ReviewLibraryAuditRow(job_id=jid,review_id=rid,status='verified' if rid=='unaffected' else 'attention'))
        db.flush();before=policy_version(db)
        result=upgrade_policy(db,jid);db.flush()
        job=db.get(Job,jid)
        assert result['reopened']==2 and job.result['policy_version']==policy_version(db)!=before
        assert job.result['stages'][0]['cursor']=='unaffected'
        assert job.result['checked']==job.result['verified']==1 and job.result['attention']==0
        assert db.get(ReviewLibraryAuditRow,(jid,'unaffected')).status=='verified'
        assert db.get(ReviewLibraryAuditRow,(jid,'mileage')).status=='retry'
        assert len(list(db.scalars(select(Memory).where(Memory.enabled==True))))==20
        assert setting(db,UPGRADE,{})['previous_text']==OLD_RULE
        assert upgrade_policy(db,jid)=={'already_applied':True}
