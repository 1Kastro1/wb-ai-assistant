"""Bounded durable reuse of successful checks in an equivalent buyer context.

A similar-looking keyword is not a match. Rules, product, verified facts, rating,
whole buyer context and name visibility must match; changed policy opts out.
Only audited no-claims praise can share a group across equivalent short texts.
"""
from copy import deepcopy
import hashlib
import json
import re
import time
import unicodedata
from sqlalchemy import select
from .db import Setting, IS_SQLITE

BUCKET_LIMIT = 64
MAX_AGE = 14 * 86400


def context_key(review, product, rules, facts, *, praise_group=False, candidate_group=False):
    from .rule_execution import VERSION
    from .shared_marketplaces import POLICY_VERSION
    if (not review or not product or review.marketplace.endswith('_question') or
        review.manual or review.safety_critical or review.risk != 'NORMAL'):
        return None
    def fields(obj, names):
        return {name: getattr(obj, name, None) for name in names}
    message = re.sub(r'\s+', ' ', review.text).strip()
    if candidate_group:
        from .reply_policy import analyze_customer_message
        from .vehicle_identifiers import vehicle_identifiers
        analysis = analyze_customer_message(review.text, review.rating)
        if (not analysis['complaints'] or analysis['is_question'] or analysis['buyer_asks_about_money']
                or vehicle_identifiers(review.text)):
            return None
        # This key finds a drafting example, NEVER a reusable approval. Every
        # different buyer message must undergo a fresh complete rule check.
        message = 'candidate:' + json.dumps([sorted(analysis['complaints']), analysis['sentiment']], ensure_ascii=False)
    if praise_group:
        from .rule_execution import simple_positive, praise_text
        if not simple_positive(review):
            return None
        group = ('empty-rating' if not review.text.strip() else 'scent-praise' if praise_text(review) in
            {'приятный запах','приятный аромат','аромат приятный','запах приятный','запах пробивается как оригинал'} else 'general-praise')
        message = 'audited-positive:' + group
    data = {'version': VERSION, 'policy_version': POLICY_VERSION, 'rules': list(rules), 'facts': facts,
        'review': fields(review, ('wb_account_id','marketplace','rating','risk','manual','safety_critical',
                                  'buyer_name','customer_name','buyer_name_hidden','topics')),
        'message': message,
        'product': fields(product, ('id','name','brand','category','part_number','facts','marketplace'))}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def load_template(db, review, product, rules, facts, assess, *, previous='', exact_answer=None):
    from .owner_reply_policy import saved_rule_quality, local_issues
    from .safety import validate_reply
    from .rule_execution import verified_positive_reply
    for grouped in (False, True):
        key = context_key(review, product, rules, facts, praise_group=grouped)
        if not key:
            continue
        row = db.get(Setting, 'verified-replies:' + key[0])
        entry = deepcopy((row.value or {}).get(key)) if row else None
        for item in _variants(entry, db):
            if time.time()-float(item.get('at',0)) > MAX_AGE:
                continue
            answer, verdict = item.get('answer',''), item.get('verdict') or {}
            if exact_answer is not None and answer != exact_answer:
                continue
            if (same_answer(answer, previous) or not verdict.get('checked') or not verdict.get('passed')
                    or verdict.get('technical_error')):
                continue
            try:
                if (grouped and not verified_positive_reply(answer,review,rules,product=product) or
                    validate_reply(answer) != answer or local_issues(answer,rules,review) or
                    not saved_rule_quality(answer,rules,verdict,review=review)['passed'] or not assess(answer)['passed']):
                    continue
            except ValueError:
                continue
            verdict.update(verification_method='learned_positive_group' if grouped else 'learned_exact_context',
                           template_source=item.get('source',''),verification_seconds=0, template_key=key)
            return answer, verdict
    return None


def same_answer(first, second):
    def normalized(value):
        return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value)).strip().casefold()
    return bool(second) and normalized(first) == normalized(second)


def _variants(entry, db=None):
    result=[]
    for item in deepcopy(entry.get('variants') or [entry]) if entry else []:
        if item.get('template_id') and not item.get('answer') and db is not None:
            tid=item['template_id']
            row=db.get(Setting,'verified-texts:'+tid[0])
            item['answer']=((row.value or {}).get(tid) or {}).get('answer','') if row else ''
        if item.get('answer') and not any(same_answer(item['answer'],v['answer']) for v in result):
            result.append(item)
    return result


def _remember_text(db, answer):
    # One content-addressed body shared by independently approved contexts.
    # Bounded buckets avoid an ever-growing library of orphaned variants.
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    tid=hashlib.sha256(answer.encode()).hexdigest()
    bucket='verified-texts:'+tid[0]
    insert=sqlite_insert if IS_SQLITE else pg_insert
    db.execute(insert(Setting).values(key=bucket,value={}).on_conflict_do_nothing(index_elements=['key']))
    row=db.scalar(select(Setting).where(Setting.key==bucket).with_for_update().execution_options(populate_existing=True))
    values=dict(row.value or {})
    values[tid]={'answer':answer,'at':time.time()}
    row.value=dict(sorted(values.items(),key=lambda value:value[1]['at'],reverse=True)[:BUCKET_LIMIT*6])
    return tid


def similar_candidate(db, review, product, rules, facts, assess, *, previous=''):
    key = context_key(review, product, rules, facts, candidate_group=True)
    if not key:
        return None
    row = db.get(Setting, 'verified-replies:' + key[0])
    for item in _variants((row.value or {}).get(key) if row else None, db):
        verdict, answer = item.get('verdict') or {}, item.get('answer', '')
        if (time.time()-float(item.get('at',0)) <= MAX_AGE and not same_answer(answer, previous)
                and verdict.get('checked') is True and verdict.get('passed') is True
                and not verdict.get('technical_error') and assess(answer)['passed']):
            return answer
    return None


def remember_template(db, review, product, rules, facts, draft):
    from .owner_reply_policy import saved_rule_quality
    key = context_key(review, product, rules, facts)
    quality = draft.quality or {}
    verdict = quality.get('owner_rules') or {}
    if (not key or quality.get('passed') is not True or quality.get('text_issues') or
        verdict.get('checked') is not True or verdict.get('passed') is not True or
        verdict.get('technical_error') or verdict.get('issues') or
        not saved_rule_quality(draft.text,rules,verdict,review=review)['passed']):
        return False
    # Insert the bounded bucket without a racing read-then-insert. The row lock
    # serializes updates across API/worker processes; the caller owns the commit.
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from .rule_execution import verified_positive_reply
    keys = [key]
    similar = context_key(review, product, rules, facts, candidate_group=True)
    if similar: keys.append(similar)
    if verified_positive_reply(draft.text,review,rules,product=product):
        grouped = context_key(review,product,rules,facts,praise_group=True)
        if grouped: keys.append(grouped)
    template_id = _remember_text(db, draft.text)
    for key in sorted(set(keys)):
        bucket = 'verified-replies:' + key[0]
        insert = sqlite_insert if IS_SQLITE else pg_insert
        db.execute(insert(Setting).values(key=bucket,value={}).on_conflict_do_nothing(index_elements=['key']))
        row = db.scalar(select(Setting).where(Setting.key==bucket).with_for_update().execution_options(populate_existing=True))
        values = dict(row.value or {})
        item = {'template_id':template_id,'verdict':deepcopy(verdict),'source':draft.id,'at':time.time()}
        variants = [item] + [v for v in _variants(values.get(key),db) if not same_answer(v.get('answer',''),draft.text)]
        # Existing inline entries remain readable; new bodies are stored once.
        variants = [{k:v for k,v in value.items() if k!='answer'} if value.get('template_id') else value for value in variants]
        values[key] = {**item, 'variants':variants[:6]}
        row.value = dict(sorted(values.items(),key=lambda item:item[1]['at'],reverse=True)[:BUCKET_LIMIT])
    return True




def compact_library(db):
    """Convert existing copies to shared bodies without inventing approvals."""
    snapshots=[(row.key,deepcopy(row.value or {})) for row in db.scalars(
        select(Setting).where(Setting.key.startswith('verified-replies:')).order_by(Setting.key))]
    prepared={};answers={}
    for bucket,values in snapshots:
        prepared[bucket]={}
        for key,entry in values.items():
            variants=[]
            for item in _variants(entry,db):
                if time.time()-float(item.get('at',0))>MAX_AGE:continue
                answer=item.pop('answer');tid=hashlib.sha256(answer.encode()).hexdigest()
                answers[tid]=answer;variants.append({**item,'template_id':tid})
            if variants:prepared[bucket][key]={**variants[0],'variants':variants[:6]}
    # Same lock order as ordinary learning: bodies before context buckets.
    for tid,answer in sorted(answers.items()):_remember_text(db,answer)
    for bucket,values in sorted(prepared.items()):
        row=db.scalar(select(Setting).where(Setting.key==bucket).with_for_update().execution_options(populate_existing=True))
        # Activation runs with the worker stopped; skip concurrent API learning
        # rather than replacing a newly saved approval with an old snapshot.
        if row.value==dict(snapshots)[bucket]:row.value=values
    db.flush()
    return {'unique_templates':len(answers),'contexts':sum(len(v) for v in prepared.values())}
