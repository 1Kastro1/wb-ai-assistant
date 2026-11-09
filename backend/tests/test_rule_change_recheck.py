import asyncio
from sqlalchemy import select
from app.db import Session, Product, Review, Draft, Memory, Job, StoreProfile, Action, User
from app.rule_change_recheck import snapshot, rule_changed, scope_conditions, affected, should_check, enqueue_draft
from app.owner_reply_policy import fingerprint
from app.owner_reply_rules import matching_owner_rules
from app.draft_audit import recheck_step


def seed(db):
    for account, market in [('owner', 'wb'), ('oz', 'ozon'), ('ya', 'yandex')]:
        if account != 'owner':
            db.add(StoreProfile(id=account, provider=market, name=market))
        db.add(Product(id=account, name='Воздушный фильтр', brand='TEST', part_number='AA-1', marketplace=market))
        db.flush()
        for suffix in ('review', 'question', 'sent', 'publishing'):
            rid = account+suffix
            db.add(Review(id=rid, wb_review_id=rid, wb_account_id=account, product_id=account,
                marketplace=market+('_question' if suffix=='question' else ''), text='Отличный фильтр', rating=5,
                is_answered=suffix=='sent', status='publishing' if suffix=='publishing' else 'draft_ready'))
            db.flush()
            db.add(Draft(id=rid, review_id=rid, text='Здравствуйте. Спасибо за отзыв.',
                         original='Здравствуйте. Спасибо за отзыв.', revision=4))
    db.add(Product(id='unrelated', name='Ароматизатор', brand='OTHER', part_number='B-1'))
    db.flush()
    db.add(Review(id='unrelated', wb_review_id='unrelated', wb_account_id='owner', product_id='unrelated',
                  marketplace='wb', text='Все супер', rating=5))
    db.flush()
    db.add(Draft(id='unrelated', review_id='unrelated', text='Здравствуйте. Спасибо за отзыв.', original='Здравствуйте. Спасибо за отзыв.'))
    db.commit()


def jobs(db):
    return list(db.scalars(select(Job).where(Job.result['rule_change'].as_boolean()==True)))


def test_memory_api_mutations_create_durable_scoped_checks_once(client):
    with Session() as db: seed(db)
    body = {'text':'Пиши естественно, без повторов.', 'kind':'REVIEW_RULE', 'scope':'brand:TEST',
            'operation_id':'rule-create-unique-0001'}
    response = client.post('/memory', json=body)
    assert response.status_code == 200, response.text
    mid = response.json()['id']
    assert client.post('/memory', json=body).status_code == 200
    with Session() as db:
        assert len(jobs(db)) == 3
        assert all(j.result['check_only'] and j.result['policy_scan'] for j in jobs(db))
    # Changing kind targets the old review audience AND the new questions.
    assert client.patch('/memory/'+mid+'/kind', json={'kind':'QUESTION_RULE'}).status_code == 200
    with Session() as db:
        changed = [j for j in jobs(db) if len(j.result['rule_scopes']) == 2]
        assert len(changed) == 3
        state = changed[0].result
        for suffix in ('review', 'question'):
            r = db.get(Review, state['account_id']+suffix)
            assert affected(db, r, db.get(Product, r.product_id), state)
        assert not affected(db, db.get(Review,'unrelated'),db.get(Product,'unrelated'),state)
    assert client.patch('/memory/'+mid+'/enabled',json={'enabled':False}).status_code == 200
    with Session() as db: count = len(jobs(db))
    # Repeated no-op and deletion of an already disabled rule do not add work.
    assert client.patch('/memory/'+mid+'/enabled',json={'enabled':False}).status_code == 200
    assert client.delete('/memory/'+mid).status_code == 200
    with Session() as db: assert len(jobs(db)) == count


def test_product_scope_reaches_equivalent_listings_and_keeps_saved_text(client, monkeypatch):
    with Session() as db:
        seed(db)
        rule = Memory(id='rule', text='Не повторяй благодарность.', kind='REVIEW_RULE', scope='product:owner', enabled=True)
        db.add(rule)
        created = rule_changed(db,None,snapshot(rule))
        ids=[j.id for j in created]
        assert len(ids)==3
        assert [j.id for j in rule_changed(db,None,snapshot(rule))]==ids
        db.commit()
        original={d.id:(d.text,d.revision) for d in db.scalars(select(Draft))}
    called=[]
    async def verify(answer, review, product, rules, facts, assess, **kwargs):
        assert kwargs['check_only'] is True
        called.append(review.id)
        return answer, {'fingerprint':fingerprint(rules),'checked':True,'passed':True,'issues':[]}
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules', verify)
    for jid in ids: asyncio.run(recheck_step(jid))
    assert set(called)=={'ownerreview','ozreview','yareview'}
    with Session() as db:
        assert original=={d.id:(d.text,d.revision) for d in db.scalars(select(Draft))}
        assert all(db.get(Job,jid).status=='completed' for jid in ids)


def test_scoped_rules_skip_unaffected_and_already_current_verdict(client):
    with Session() as db:
        seed(db)
        rule=Memory(id='r',text='Пиши по существу.',kind='REVIEW_RULE',scope='category:Фильтры',enabled=True)
        db.add(rule); state=rule_changed(db,None,snapshot(rule))[0].result
        review=db.get(Review,'ownerreview');draft=db.get(Draft,review.id);product=db.get(Product,review.product_id)
        rules=matching_owner_rules(db,product)
        assert should_check(db,draft,review,product,state,rules)
        draft.quality={'passed':True,'owner_rules':{'checked':True,'passed':True,'fingerprint':fingerprint(rules),'issues':[]}}
        assert not should_check(db,draft,review,product,state,rules)
        # Curated category metadata is respected even if the marketplace category is empty.
        assert affected(db,review,product,state)
        other=db.get(Review,'unrelated')
        assert not should_check(db,db.get(Draft,other.id),other,db.get(Product,other.product_id),state)


def test_backlog_uses_one_job_and_never_resumes_cancelled_checks(client):
    with Session() as db:
        seed(db)
        first=db.get(Review,'ownerreview'); second=db.get(Review,'unrelated')
        job=enqueue_draft(db,db.get(Draft,first.id),first)
        assert enqueue_draft(db,db.get(Draft,second.id),second).id==job.id
        job.status='cancelled';job.cancel_requested=True;db.commit()
        assert enqueue_draft(db,db.get(Draft,second.id),second).id==job.id
        assert job.status=='cancelled' and job.cancel_requested
        assert len(jobs(db))==1


def test_deleted_active_rule_queues_check_and_transaction_rollback_keeps_queue_atomic(client):
    with Session() as db:
        seed(db)
        rule=Memory(id='r',text='Пиши по существу.',kind='REVIEW_RULE',scope='global',enabled=True)
        db.add(rule);db.commit()
        before=snapshot(rule);db.delete(rule);rule_changed(db,before,None)
        assert len(jobs(db))==3
        db.rollback()
        assert db.get(Memory,'r') and not jobs(db)
    assert client.delete('/memory/r').status_code==200
    with Session() as db: assert len(jobs(db))==3
