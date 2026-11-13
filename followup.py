"""Requeue context errors, measure real examples, repair one untouched empty draft."""
import asyncio
import ast
import inspect
import json
from sqlalchemy import select
from app.db import Session, ReviewLibraryAuditRow, Review, Product, Draft
from app.library_workspace import latest_job
from app.reply_policy import generation_policy, has_reply_body
from app.model_context import draft_messages, request_tokens, fitting_rule_groups
from app.owner_reply_rules import matching_owner_rules
from app.owner_reply_policy import context_constraints, semantic_rules, rule_groups, CHECK_SCHEMA, enforce_owner_rules
from app.services import review_verified_facts, generate_draft
from app.rule_execution import select_rules
from app.draft_audit import manually_edited
from app.review_library_audit import publication_protected

targets = [('led','albhb460w','через месяц'),('filter','4sf-103','хорошей цене'),('shovel','isus-19','лопатка')]
warm_id = None
with Session() as db:
    job = latest_job(db)
    assert job and job.status in ('queued','running') and not job.cancel_requested
    rows = list(db.scalars(select(ReviewLibraryAuditRow).where(
        ReviewLibraryAuditRow.job_id == job.id, ReviewLibraryAuditRow.status == 'blocked')))
    bad = [row for row in rows if any('контекст' in str(issue).lower() for issue in (row.issues or []))]
    snapshot = {'rows': [{'review_id':row.review_id,'status':row.status,'retry_at':row.retry_at} for row in bad], 'drafts':[]}
    for row in bad:
        row.status,row.retry_at = 'retry',0
    for index,(label,article,phrase) in enumerate(targets):
        pairs = db.execute(select(Review,Product).join(Product,Product.id==Review.product_id)
            .where(Product.name.ilike('%'+article+'%'))).all()
        pair = next(((review,product) for review,product in pairs if phrase in review.text.lower()),None)
        if pair is None:
            print({'case':label,'found':False},flush=True)
            continue
        review,product=pair
        rules=matching_owner_rules(db,product)
        facts=review_verified_facts(db,review,product,rules)
        messages=draft_messages(generation_policy(),{'buyer_message':review.text,'rating':review.rating,
            'product':product.name,'required_style':{key:context_constraints(rules,review)[key]
                for key in ('greeting','greetings','signature')}},select_rules(rules,review,product)[0],facts)
        row=db.get(ReviewLibraryAuditRow,(job.id,review.id))
        if row and row.status in ('retry','blocked'):
            row.status,row.retry_at='retry',-100+index
        source=ast.parse(inspect.getsource(enforce_owner_rules))
        system=next(ast.literal_eval(node.value.elts[0]) for node in ast.walk(source)
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='messages' for t in node.targets))
        draft=db.scalar(select(Draft).where(Draft.review_id==review.id))
        payload={'buyer_message':review.text,'rating':review.rating,'product':product.name,
            'verified_facts':facts,'answer':review.existing_answer or (draft.text if draft else ''),
            'reply_kind':'review','required_style':{key:context_constraints(rules,review)[key]
                for key in ('greeting','greetings','signature')},'protected_answer':review.risk!='NORMAL'}
        groups=fitting_rule_groups(rule_groups(semantic_rules(select_rules(rules,review,product)[0])),system,payload,CHECK_SCHEMA)
        check_sizes=[request_tokens([system,{'role':'user','content':json.dumps({**payload,'owner_rules':group},ensure_ascii=False)+'\n/no_think'}],CHECK_SCHEMA) for group in groups]
        print({'case':label,'found':True,'preparation_tokens':request_tokens(messages,predict=768),
               'full_rule_count':len(rules),'verification_groups':len(groups),'verification_tokens':check_sizes},flush=True)
    candidates=db.execute(select(Review,Draft).join(Product,Product.id==Review.product_id)
        .join(Draft,Draft.review_id==Review.id).where(Product.name.ilike('%AB5001%'),
            Review.is_answered==False)).all()
    for review,draft in candidates:
        if ('нагруз' in review.text.lower() and not has_reply_body(draft.text)
                and not manually_edited(draft) and not publication_protected(db,review)):
            warm_id=review.id
            snapshot['drafts'].append({'id':draft.id,'review_id':review.id,'text':draft.text,
                                      'revision':draft.revision,'quality':draft.quality})
            break
    with open('/tmp/settings-warm-before.json','w',encoding='utf-8') as file:
        json.dump(snapshot,file,ensure_ascii=False)
    job.result={**job.result,'retry_at':0,'preparation_context_fix':'2026-11-13'}
    job.status='queued'
    db.commit()
    print({'requeued_context_cases':len(bad),'empty_lamp_draft_found':bool(warm_id)},flush=True)

async def warm():
    if not warm_id:return
    with Session() as db:
        draft=await generate_draft(db,warm_id,instruction='Сделай ответ тёплым и содержательным: поблагодари за опыт, учти вопрос о мощности, не выдумывай характеристики и результаты измерений.')
        print({'warm_repair':{'review_id':warm_id,'text':draft['text'],
            'has_body':has_reply_body(draft['text']),'passed':draft['quality'].get('passed'),
            'rules_checked':draft['quality'].get('owner_rules',{}).get('checked')}},flush=True)

asyncio.run(warm())
