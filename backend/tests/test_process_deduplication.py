from app.db import VerifiedReplyText,VerifiedReplyContext
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from app import services
from app.db import Session, Product, Review, Draft, Memory, Job, Setting
from app.owner_reply_rules import matching_owner_rules
from app.owner_reply_policy import enforce_owner_rules
from app.rule_retry import enqueue
from app.draft_audit import recheck_step
from test_new_variant_context import seed


def test_unchanged_verifier_repair_is_not_submitted_twice(client, monkeypatch):
    reply='Здравствуйте. Спасибо за отзыв.'
    judge=AsyncMock(return_value={'compliant':False,'violations':['Нет объяснения'], 'revised_answer':reply})
    monkeypatch.setattr(services.ollama,'structured',judge)
    review=SimpleNamespace(text='Не понравилось',rating=2,marketplace='wb')
    product=SimpleNamespace(name='Товар',brand='TEST',category='')
    answer, verdict=asyncio.run(enforce_owner_rules(reply,review,product,
        ['Объясняй причину замечания покупателя.'],[],lambda v:{'passed':True}))
    assert answer==reply and not verdict['passed']
    assert verdict['issues']==['Нет объяснения']
    assert judge.await_count==1


def test_identical_whole_rules_keep_narrower_precedence_and_question_scope(client):
    with Session() as db:
        p=Product(id='p',name='Товар',brand='TEST');db.add(p)
        db.add_all([Memory(id='a',text='Начинай с «Добрый день!».',scope='global',kind='REVIEW_RULE'),
            Memory(id='b',text='Начинай с «Здравствуйте».',scope='global',kind='REVIEW_RULE'),
            Memory(id='c',text='Начинай с «Добрый день!».',scope='brand:TEST',kind='REVIEW_RULE'),
            Memory(id='q',text='Ответь на вопрос по существу.',scope='global',kind='QUESTION_RULE')]);db.commit()
        assert matching_owner_rules(db,p)==['Начинай с «Здравствуйте».','Начинай с «Добрый день!».']
        assert matching_owner_rules(db,p,for_questions=True)==['Ответь на вопрос по существу.']


def test_recheck_reuses_pending_singleton_but_not_cancelled_or_other_store(client):
    from test_technical_rule_retry import seed_error
    seed_error()
    with Session() as db:
        db.add(Job(id='batch',kind='draft_recheck',result={'account_id':'owner','check_only':True,
            'draft_ids':['previous','d'],'cursor':1}));db.commit()
        d,r=db.get(Draft,'d'),db.get(Review,'r')
        assert enqueue(db,d,r,manual=True).id=='batch'
        db.get(Job,'batch').cancel_requested=True;db.commit()
        new=enqueue(db,d,r,manual=True);db.flush()
        assert new.id!='batch'
        assert enqueue(db,d,r,manual=True).id==new.id
        assert len(list(db.scalars(select(Job))))==2


@pytest.mark.parametrize('changed', [None,'rule','facts','answer'])
def test_duplicate_checks_reuse_only_current_exact_persisted_success(client,monkeypatch,changed):
    check=AsyncMock(return_value={'compliant':True,'violations':[],'revised_answer':''})
    monkeypatch.setattr(services.ollama,'structured',check)
    with Session() as db:
        seed(db,'wb')
        first=asyncio.run(services.generate_draft(db,'buyer'))
        assert first['quality']['passed']
        assert check.await_count
        if changed=='rule': db.add(Memory(id='new',text='Новое требование к ответу.',kind='REVIEW_RULE'))
        elif changed=='facts': db.get(Product,'lotion').facts=[{'text':'Новая инструкция','verification_status':'VERIFIED'}]
        elif changed=='answer': db.get(Draft,'bad').text += ' Дополнительный текст.'
        db.add_all(Job(id=jid,kind='draft_recheck',result={'account_id':'owner','check_only':True,'draft_ids':['bad']}) for jid in ('one','two'))
        db.commit()
    check.reset_mock()
    check.return_value={'compliant':False,'violations':['Нужна проверка'],'revised_answer':''}
    for jid in ('one','two'): asyncio.run(recheck_step(jid))
    with Session() as db:
        assert bool(check.await_count) is bool(changed)
        if not changed:
            d=db.get(Draft,'bad')
            assert d.text==first['text'] and d.quality['passed']
            assert d.quality['owner_rules']['verification_method']=='learned_exact_context'
            rows=list(db.scalars(select(VerifiedReplyContext)))
            assert len(rows)==len({(r.context_key,r.kind,r.template_id) for r in rows})

def test_candidates_deduplicated_after_required_style(client,monkeypatch):
    with Session() as db:
        p=Product(id='p',name='Товар',brand='TEST');db.add(p);db.flush()
        db.add(Review(id='r',wb_review_id='r',product_id='p',text='Быстрая доставка',rating=4))
        db.add(Memory(id='hello',text='Каждый ответ начинай с «Добрый день!».',kind='REVIEW_RULE'));db.commit()
        monkeypatch.setattr(services,'rule_reply',lambda *a,**k:'Здравствуйте! Спасибо за отзыв. Рады, что доставка была быстрой.')
        result=asyncio.run(services.generate_draft(db,'r'))
        assert result['quality']['candidates_evaluated']==1
        assert result['text'].startswith('Добрый день!')
