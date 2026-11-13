import asyncio
import pytest
from sqlalchemy import select
from app import scheduled_publication as automatic
from app.db import Session, Review, Draft, Product, Job
from app.safety import rule_reply
from app.services import assess_reply
from test_scheduled_publication import ready, enable, jobs


def negative(db, store):
    review = db.get(Review, store+'review'); draft = db.get(Draft, store+'review')
    review.rating, review.status, review.text = 2, 'manual_review', ''
    draft.text = draft.original = rule_reply(review, db.get(Product, review.product_id))
    draft.quality = assess_reply(review, db.get(Product, review.product_id), draft.text)
    assert draft.quality['passed'], draft.quality
    db.commit()


@pytest.mark.parametrize('provider', ['owner','ya'])
def test_low_template_scope_publishes_in_every_market_once(client, ready, provider):
    with Session() as db: negative(db, provider)
    result = enable(client, publication_reviews=False, publication_questions=False, publication_low_ratings=True)
    assert result['publication_low_ratings'] is True
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert [row[1] for row in ready] == [provider+'review']
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert len(ready) == 1


@pytest.mark.parametrize('change',['default_off','human_edit','wrong_template','safety','manual','unknown_complaint','unchecked'])
def test_low_rating_template_cannot_bypass_checks(client, ready, change):
    with Session() as db:
        negative(db, 'owner')
        r,d = db.get(Review,'ownerreview'),db.get(Draft,'ownerreview')
        if change=='human_edit': d.edits=[{'by':'owner','old':'a','new':d.text}]
        if change=='wrong_template': d.text += ' Дополнение.'
        if change=='safety': r.safety_critical=True
        if change=='manual': r.manual=True
        if change=='unknown_complaint': r.text='Товар сломался через день'
        if change=='unchecked': d.quality={**d.quality,'owner_rules':{'passed':False,'checked':False,'status':'pending'}}
        db.commit()
    enable(client, publication_reviews=False, publication_questions=change=='default_off', publication_low_ratings=change!='default_off')
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert 'ownerreview' not in [row[1] for row in ready]


def test_changing_low_rating_scope_revokes_prepared_jobs(client, ready):
    with Session() as db: negative(db,'owner')
    enable(client,publication_low_ratings=True)
    previous=jobs()
    assert client.patch('/settings/schedule',json={'publication_low_ratings':False}).status_code==200
    for jid in previous: asyncio.run(automatic.run_step(jid))
    assert not ready


@pytest.mark.parametrize('text', ['Аромат слабый, верните деньги','Аромат слабый и болит голова','Не пахнет. Банка протекла','Аромат слабый, упаковка порвана','Аромат слабый. Ребёнок попробовал содержимое','Аромат слабый. Испортилась панель','Аромат слабый 🤮','Аромат слабый 火'])
def test_mixed_or_sensitive_scent_is_not_a_template(text):
    from types import SimpleNamespace
    from app.low_rating_templates import template_key
    review=SimpleNamespace(text=text,rating=2,marketplace='wb',risk='NORMAL',manual=False,safety_critical=False)
    product=SimpleNamespace(name='Меловой ароматизатор EIKOSHA',brand='EIKOSHA')
    assert template_key(review,product) is None


@pytest.mark.parametrize('name,brand,text',[
    ('Меловой ароматизатор для авто MARINE BLUE SQUASH A-106','EIKOSHA','Аромат в машине так себе звучит.....'),
    ('Меловой ароматизатор для авто MARINE BLUE SQUASH A-106','EIKOSHA','Слабо пахнет'),
    ('Полироль для автомобиля защитный Fusso 12 Months','SOFT99','Воск хороший, но не стоит ждать чудо, паутину видно'),
])
def test_product_template_matches_the_actual_generation_route(client,ready,name,brand,text):
    from app.low_rating_templates import approved_template
    from app.owner_reply_policy import apply_local_preferences,apply_signature
    from app.product_guidance import fusso_instruction
    from app.services import trusted_product_facts
    with Session() as db:
        r,d,p=db.get(Review,'ownerreview'),db.get(Draft,'ownerreview'),db.get(Product,'owner')
        p.name,p.brand=name,brand;r.rating,r.text=3,text
        facts=trusted_product_facts(db,p)
        if fusso_instruction(p):facts+=[fusso_instruction(p)]
        answer,_=apply_local_preferences(rule_reply(r,p,facts=facts),r,[])
        d.text=d.original=apply_signature(answer,[]);db.commit()
        assert approved_template(db,r,d)


def test_ozon_low_empty_rating_is_preserved_and_skipped(client,ready):
    with Session() as db: negative(db,'oz')
    enable(client,publication_reviews=False,publication_questions=False,publication_low_ratings=True)
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert not ready
    with Session() as db:
        job=db.scalar(select(Job).where(Job.kind==automatic.KIND,Job.result['account_id'].as_string()=='oz'))
        assert job.result['skipped']==0 and job.result['uncertain']==0
        assert job.result['scanned']==0  # excluded before scanning or reserving
        assert db.get(Draft,'ozreview').text
