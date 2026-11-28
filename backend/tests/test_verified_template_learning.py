from app.db import VerifiedReplyText,VerifiedReplyContext
import asyncio
from unittest.mock import patch
from sqlalchemy import select
from app.db import Session,Product,Review,Draft,Memory,Setting
from app import services
from app.rule_execution import positive_template
from app.owner_reply_rules import matching_owner_rules
from app.owner_reply_policy import enforce_owner_rules,attach_rule_quality
from app.rule_retry import enqueue
from app.draft_audit import recheck_step
from app.learned_reply_templates import load_template
from app.review_rule_pack143 import PACK


def test_template_view_deduplicates_review_text_across_markets(client):
    from app.verified_reply_library import remember

    with Session() as db:
        for market in ('wb', 'ozon'):
            remember(db, 'review-' + market, 'exact', 'Спасибо за Ваш отзыв!', {}, marketplace=market)
        remember(db, 'question', 'exact', 'Да, подходит.', {}, marketplace='wb_question')
        db.commit()
    result = client.get('/review-library/templates', params={'marketplace': 'ozon', 'q': 'СПАСИБО'})
    assert result.status_code == 200
    data = result.json()
    assert data['total'] == 1
    assert data['items'][0]['marketplaces'] == ['wb', 'ozon']
    assert data['items'][0]['contexts'] == 2
    assert client.get('/review-library/templates').json()['total'] == 1


def seed(db,text='Все супер!'):
    db.add(Product(id='p',name='Ароматизатор GIGA',brand='EIKOSHA',category='Ароматизаторы',marketplace='wb'))
    db.add_all(Memory(id='m'+str(i),text=r['text'],scope=r['scope'],kind='REVIEW_RULE') for i,r in enumerate(PACK['rules']))
    db.flush()
    db.add(Review(id='first',wb_review_id='first',product_id='p',wb_account_id='owner',marketplace='wb',text=text,rating=5,risk='NORMAL',manual=False))
    db.flush()
    p,r=db.get(Product,'p'),db.get(Review,'first')
    rules=matching_owner_rules(db,p)
    answer=positive_template(r,rules,product=p)
    db.add(Draft(id='saved',review_id=r.id,text=answer,original=answer,quality={'passed':False,'owner_rules':{'technical_error':True,'passed':False,'checked':False}}))
    db.flush()
    job=enqueue(db,db.get(Draft,'saved'),r)
    db.commit()
    return job.id,answer


def test_owner_edit_shows_review_categories_and_needs_fresh_proof(client):
    from app.learned_reply_templates import edited_candidate
    from app.verified_reply_library import remember, text_id

    with Session() as db:
        jid, old = seed(db)
    asyncio.run(recheck_step(jid))
    item = client.get('/review-library/templates').json()['items'][0]
    assert item['categories'] == ['Положительный отзыв']
    assert item['products'] == ['Ароматизаторы']
    assert item['ratings'] == [5]
    endpoint = '/review-library/templates/' + item['id']
    opening, rest = old.split('! ', 1)
    signature = rest.rsplit('\n', 1)[-1]
    revised = opening + '! Благодарим за то, что поделились своим мнением.\n' + signature
    assert client.patch(endpoint, json={'text': revised, 'revision': 0}).json()['revision'] == 1
    assert client.patch(endpoint, json={'text': old, 'revision': 0}).status_code == 409
    data = client.get('/review-library/templates', params={'q': 'БЛАГОДАРИМ'}).json()
    assert [entry['text'] for entry in data['items']] == [revised]
    with Session() as db:
        r, p = db.get(Review, 'first'), db.get(Product, 'p')
        rules = matching_owner_rules(db, p)
        facts = services.review_verified_facts(db, r, p, rules)
        assert edited_candidate(db, r, p, rules, facts, lambda value: {'passed': True}) == revised
        assert load_template(db, r, p, rules, facts, lambda value: {'passed': True}, exact_answer=revised) is None
        assert edited_candidate(db, r, p, rules + ['Новое правило'], facts, lambda value: {'passed': True}) is None
        assert db.get(Draft, 'saved').text == old
        remember(db, 'independently-verified', 'exact', revised, {}, source=r.id, marketplace='wb')
        db.commit()
    result = client.get('/review-library/templates').json()
    assert result['total'] == 1
    assert result['items'][0]['id'] == text_id(old)


def test_successful_separate_recheck_is_learned_and_reused_after_new_session(client,monkeypatch):
    from app.integrations import ollama
    def no_model(*a,**k): raise AssertionError('Audited learned praise must not call model')
    monkeypatch.setattr(ollama,'structured',no_model)
    monkeypatch.setattr(ollama,'reply_variants',no_model)
    with Session() as db: jid,answer=seed(db)
    asyncio.run(recheck_step(jid))
    with Session() as db:
        assert db.get(Draft,'saved').quality['passed']
        assert list(db.scalars(select(VerifiedReplyContext)))
        db.add(Review(id='next',wb_review_id='next',product_id='p',wb_account_id='owner',marketplace='wb',text='Все отлично!',rating=5,risk='NORMAL',manual=False));db.commit()
        result=asyncio.run(services.generate_draft(db,'next'))
        assert result['text']==answer
        assert result['model']=='verified-template-1.0'
        assert result['quality']['owner_rules']['verification_method']=='learned_positive_group'
        assert result['quality']['passed']


def test_group_learning_never_crosses_complaints_questions_empty_text_or_policy_changes(client):
    with Session() as db: jid,answer=seed(db,'Приятный запах.')
    asyncio.run(recheck_step(jid))
    with Session() as db:
        r,p=db.get(Review,'first'),db.get(Product,'p')
        rules=matching_owner_rules(db,p)
        facts=services.review_verified_facts(db,r,p,rules)
        r.text='Аромат приятный!'
        assert load_template(db,r,p,rules,facts,lambda value:{'passed':True})
        for text in ('Приятный запах, но быстро выветрился','Как открыть?',''):
            r.text=text
            assert load_template(db,r,p,rules,facts,lambda value:{'passed':True}) is None
        r.text='Аромат приятный!'
        assert load_template(db,r,p,[*rules,'Новое правило'],facts,lambda value:{'passed':True}) is None
        assert load_template(db,r,p,rules,[*facts,{'text':'changed'}],lambda value:{'passed':True}) is None


def test_failed_recheck_never_enters_library(client,monkeypatch):
    from app import owner_reply_policy
    with Session() as db: jid,answer=seed(db)
    async def timeout(text,*a,**k):
        return text,{'passed':False,'checked':False,'technical_error':True,'issues':['timeout']}
    monkeypatch.setattr(owner_reply_policy,'enforce_owner_rules',timeout)
    asyncio.run(recheck_step(jid))
    with Session() as db:
        assert not list(db.scalars(select(VerifiedReplyContext)))


def test_template_body_is_shared_across_contexts_and_legacy_duplicates_compact(client):
    import time
    from app.learned_reply_templates import remember_template,compact_library,_variants
    with Session() as db:jid,answer=seed(db)
    asyncio.run(recheck_step(jid))
    with Session() as db:
        r,p,d=db.get(Review,'first'),db.get(Product,'p'),db.get(Draft,'saved')
        rules=matching_owner_rules(db,p);facts=services.review_verified_facts(db,r,p,rules)
        for text in ('Все отлично!','Все супер!','Класс!'):
            r.text=text
            assert remember_template(db,r,p,rules,facts,d)
        db.add(Setting(key='verified-replies:legacy',value={'old':{'answer':answer,'at':time.time(),
            'verdict':d.quality['owner_rules'],'variants':[
                {'answer':answer,'at':time.time(),'verdict':d.quality['owner_rules']},
                {'answer':'  '+answer+'  ','at':time.time(),'verdict':d.quality['owner_rules']}]}}))
        db.flush();result=compact_library(db);db.commit()
        bodies=list(db.scalars(select(VerifiedReplyText.text)))
        assert bodies==[answer] and result['unique_templates']==1
        assert not list(db.scalars(select(Setting).where(Setting.key.startswith('verified-replies:'))))
        contexts=list(db.scalars(select(VerifiedReplyContext)))
        assert contexts and len({(c.context_key,c.kind,c.template_id) for c in contexts})==len(contexts)
        assert load_template(db,r,p,rules,facts,lambda value:{'passed':True})[0]==answer
        assert not load_template(db,r,p,rules+['Новое правило'],facts,lambda value:{'passed':True})
