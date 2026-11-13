import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.owner_reply_policy import enforce_owner_rules,attach_rule_quality
from app.rule_retry import technical_check
from app.rule_check_plan import prepare,request_json,step_deadline

PASS={'compliant':True,'violations':[],'revised_answer':''}


def inputs():
    return dict(reply='Добрый день! Спасибо, что написали. Для этой модели заявлены 55 Вт при 12 В.',
        review=SimpleNamespace(id='review',text='Может там и нет 55?',rating=4,risk='NORMAL',marketplace='yandex'),
        product=SimpleNamespace(id='lamp',name='Лампы H1 55W 12V'),
        rules=['Учитывай содержание вопроса.','Пиши тепло.','Не выдумывай характеристики.'],
        facts=[{'text':'Заявленные 55 Вт при 12 В','source':'Карточка товара'}],
        assess=lambda answer:{'passed':True},check_only=True,group_limit=1)


def test_each_durable_step_checks_one_whole_group_then_continues_without_timeout(monkeypatch):
    from app.integrations import ollama
    monkeypatch.setattr('app.owner_reply_policy.rule_groups',lambda rules:[[r] for r in rules])
    model=AsyncMock(return_value=PASS);monkeypatch.setattr(ollama,'structured',model)
    args=inputs();previous=None
    for completed in (1,2,3):
        _,result=asyncio.run(enforce_owner_rules(**args,previous_check=previous))
        assert result['verified_groups']==completed
        assert result['resumed_groups']==completed-1
        assert model.await_count==completed
        assert result['passed']==(completed==3)
        assert result['checked']==(completed==3)
        quality=attach_rule_quality({'passed':True},result)
        if completed<3:
            assert result['continuation'] and not result['technical_error']
            assert quality['owner_rules']['status']=='pending' and technical_check(quality)
            assert not quality['passed']
        previous=result
    for call in model.await_args_list:
        payload=json.loads(call.args[0][-1]['content'])
        assert payload['verified_facts']==args['facts']
        assert payload['buyer_message']==args['review'].text
        assert call.args[1]['properties']['revised_answer']['enum']==['']
        assert 120<=call.kwargs['timeout_seconds']<=600
    assert [json.loads(c.args[0][-1]['content'])['owner_rules'] for c in model.await_args_list]==[[r] for r in args['rules']]


def test_healthy_progress_never_approves_a_later_violation(monkeypatch):
    from app.integrations import ollama
    monkeypatch.setattr('app.owner_reply_policy.rule_groups',lambda rules:[[r] for r in rules])
    model=AsyncMock(side_effect=[PASS,{'compliant':False,'violations':['Не подтверждён факт'],'revised_answer':''}])
    monkeypatch.setattr(ollama,'structured',model)
    args=inputs();_,first=asyncio.run(enforce_owner_rules(**args))
    _,failed=asyncio.run(enforce_owner_rules(**args,previous_check=first))
    assert not failed['passed'] and failed['verified_groups']==1 and not failed.get('continuation')
    assert failed['issues']==['Не подтверждён факт']


def test_timeout_preserves_completed_steps_but_does_not_certify_them_as_whole_approval(monkeypatch):
    from app.integrations import ollama
    monkeypatch.setattr('app.owner_reply_policy.rule_groups',lambda rules:[[r] for r in rules])
    model=AsyncMock(side_effect=[PASS,TimeoutError('timeout'),PASS,PASS]);monkeypatch.setattr(ollama,'structured',model)
    args=inputs();previous=None
    for expected in (1,1,2,3):
        _,previous=asyncio.run(enforce_owner_rules(**args,previous_check=previous))
        assert previous['verified_groups']==expected
        assert previous['passed']==(expected==3)
    assert model.await_count==4


def test_step_deadline_is_sized_to_full_request_but_never_unbounded():
    schema={'type':'object'}
    small=step_deadline([{'content':'Короткий вопрос'}],schema,120)
    long=step_deadline([{'content':'Характеристика. '*500}],schema,120)
    huge=step_deadline([{'content':'я'*100000}],schema,120)
    assert 120<=small<long<=600 and huge==600


def test_check_only_plan_keeps_all_evidence_and_policies_but_cannot_request_a_rewrite():
    args=inputs()
    messages,payload,schema,groups=prepare(args['review'],args['product'],args['rules'],args['facts'],args['reply'],check_only=True)
    assert [rule for group in groups for rule in group]==args['rules']
    assert payload['verified_facts']==args['facts']
    assert schema['properties']['revised_answer']['enum']==['']
    assert json.loads(request_json(payload,groups[0]))['answer']==args['reply']


@pytest.mark.parametrize('published',[False,True])
def test_archive_resumes_after_session_restart_without_editing_published_or_manual_text(monkeypatch,published):
    from app.db import Session,StoreProfile,Product,Review,Draft,Memory,ReviewLibraryAuditRow,VerifiedReplyContext
    from app.review_library_audit import start,inspect_review
    from app.integrations import ollama
    from sqlalchemy import select
    monkeypatch.setattr('app.owner_reply_policy.rule_groups',lambda rules:[[r] for r in rules])
    monkeypatch.setattr('app.services.assess_reply',lambda *args,**kwargs:{'passed':True,'issues':[],'score':96})
    model=AsyncMock(return_value=PASS);monkeypatch.setattr(ollama,'structured',model)
    with Session() as db:
        db.add(StoreProfile(id='yandex',name='Магазин',provider='yandex'))
        db.add(Product(id='p',name='Товар',marketplace='yandex'));db.flush()
        answer='Добрый день! Спасибо, что написали. Жаль, что упаковка помялась.'
        db.add(Review(id='r',wb_review_id='r',product_id='p',wb_account_id='yandex',marketplace='yandex',
            rating=4,risk='NORMAL',text='Хорошее качество, упаковка помята.',is_answered=published,existing_answer=answer if published else ''))
        db.flush()
        db.add(Draft(id='d',review_id='r',text=answer,original=answer,edits=[{'by':'employee'}],revision=7))
        db.add_all([Memory(id='one',kind='REVIEW_RULE',text='Учитывай слова об упаковке.',enabled=True),
            Memory(id='two',kind='REVIEW_RULE',text='Отвечай тепло и по существу.',enabled=True)])
        db.commit();jid=start(db).id;db.commit()
    first=asyncio.run(inspect_review(jid,'r',Session))
    assert first['status']=='retry' and first['continuation'] and model.await_count==1
    with Session() as db:
        row=db.get(ReviewLibraryAuditRow,(jid,'r'))
        assert row.verification_progress['verified_groups']==1
        assert not list(db.scalars(select(VerifiedReplyContext)))
        assert db.get(Draft,'d').text==answer and db.get(Draft,'d').revision==7
        assert db.get(Review,'r').existing_answer==(answer if published else '')
    second=asyncio.run(inspect_review(jid,'r',Session))
    assert second['status']=='verified' and model.await_count==2
    with Session() as db:
        assert db.get(ReviewLibraryAuditRow,(jid,'r')).verification_progress is None
        assert db.get(Draft,'d').text==answer and db.get(Draft,'d').revision==7
        assert db.get(Review,'r').existing_answer==(answer if published else '')


def test_streaming_recheck_continues_same_draft_without_backoff_or_cursor_skip(monkeypatch):
    from app.db import Session,Product,Review,Draft,Memory,Job
    from app.draft_audit import _recheck_one
    from app.rule_retry import enqueue
    from app.integrations import ollama
    monkeypatch.setattr('app.owner_reply_policy.rule_groups',lambda rules:[[r] for r in rules])
    monkeypatch.setattr('app.services.assess_reply',lambda *args,**kwargs:{'passed':True,'issues':[],'score':96})
    model=AsyncMock(return_value=PASS);monkeypatch.setattr(ollama,'structured',model)
    with Session() as db:
        db.add(Product(id='p',name='Товар'));db.flush()
        db.add(Review(id='r',wb_review_id='r',product_id='p',wb_account_id='owner',rating=4,risk='NORMAL',text='Хорошее качество, упаковка помята.'));db.flush()
        db.add(Draft(id='d',review_id='r',text='Добрый день! Спасибо за подробности об упаковке.',original='Добрый день! Спасибо за подробности об упаковке.',quality={'passed':False,'owner_rules':{'checked':False,'passed':False,'status':'pending'}}));db.flush()
        db.add_all([Memory(id='one',kind='REVIEW_RULE',text='Учитывай слова об упаковке.',enabled=True),
            Memory(id='two',kind='REVIEW_RULE',text='Отвечай тепло и по существу.',enabled=True)])
        jid=enqueue(db,db.get(Draft,'d'),db.get(Review,'r')).id;db.commit()
    asyncio.run(_recheck_one(jid))
    with Session() as db:
        state=db.get(Job,jid).result
        assert state.get('cursor_id','')=='' and state.get('retry_at',0)==0 and state.get('check_retries',0)==0
        rules=db.get(Draft,'d').quality['owner_rules']
        assert rules['verified_groups']==1 and rules['check_failures']==0 and rules['status']=='pending'
    asyncio.run(_recheck_one(jid))
    with Session() as db:
        assert db.get(Job,jid).result['cursor_id']=='d'
        assert db.get(Draft,'d').quality['owner_rules']['passed']
        assert model.await_count==2


def test_semantic_group_keeps_greeting_and_signature_from_full_policy(monkeypatch):
    from app.integrations import ollama
    args=inputs()
    args['rules']=['Каждый ответ начинай с «Добрый день!».','Подпись: «С уважением ГК Юником.».','Пиши тепло.']
    args['reply']='Добрый день! Спасибо, что написали. Для этой модели заявлены 55 Вт при 12 В.\nС уважением ГК Юником.'
    model=AsyncMock(return_value=PASS);monkeypatch.setattr(ollama,'structured',model)
    _,result=asyncio.run(enforce_owner_rules(**args))
    assert result['passed']
    payload=json.loads(model.await_args.args[0][-1]['content'])
    assert payload['required_style']['greeting']=='Добрый день!'
    assert payload['required_style']['signature']=='С уважением ГК Юником.'


@pytest.mark.parametrize('text,missing',[
    ('Работают, упаковано было хорошо. Разницу не заметил, может кто нагрузку мерял? Может там и нет 55?',False),
    ('Вместо двух ламп пришла одна. Какая у неё мощность?',True),
])
def test_power_questions_keep_receipt_conditions_separate_from_product_facts(text,missing):
    args=inputs();args['review'].text=text
    rule='При недостаче предложи сверить комплектность. Не обещай компенсацию.'
    messages,payload,schema,groups=prepare(args['review'],args['product'],[rule],args['facts'],args['reply'],check_only=True)
    assert ('MISSING_PARTS' in payload['buyer_context']['complaints']) is missing
    assert [r for group in groups for r in group]==[rule]
    assert payload['verified_facts']==args['facts']
    assert payload['buyer_context']['is_question']
    if not missing:assert payload['buyer_context']['recognized_case']=='lamp_power_question'
