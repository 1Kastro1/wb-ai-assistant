"""Durable reuse of successful checks in an equivalent buyer context.

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
from .db import Setting

BUCKET_LIMIT = 64
MAX_AGE = 14 * 86400


def context_key(review, product, rules, facts, *, praise_group=False, candidate_group=False, equivalent_group=False):
    from .rule_execution import VERSION
    from .reply_policy import TONE_VERSION
    from .shared_marketplaces import POLICY_VERSION
    if (not review or not product or review.marketplace.endswith('_question') or
        review.manual or review.safety_critical or review.risk != 'NORMAL'):
        return None
    def fields(obj, names):
        return {name: getattr(obj, name, None) for name in names}
    message = re.sub(r'\s+', ' ', review.text).strip()
    if equivalent_group:
        from .equivalent_reviews import message_group, VERSION as GROUP_VERSION
        group = message_group(review.text)
        if not group:return None
        message = GROUP_VERSION + ':' + group
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
    data = {'version': VERSION, 'tone_version': TONE_VERSION, 'policy_version': POLICY_VERSION, 'rules': list(rules), 'facts': facts,
        'review': fields(review, ('wb_account_id','marketplace','rating','risk','manual','safety_critical',
                                  'buyer_name','customer_name','buyer_name_hidden','topics')),
        'message': message,
        'product': fields(product, ('id','name','brand','category','part_number','facts','marketplace'))}
    if equivalent_group:
        # These are derived tags, not additional buyer circumstances. The entire
        # approved phrase, rating, product, facts, names and rules are still fixed.
        data['review'].pop('topics', None)
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def load_template(db, review, product, rules, facts, assess, *, previous='', exact_answer=None):
    from .owner_reply_policy import saved_rule_quality, local_issues
    from .safety import validate_reply
    from .rule_execution import verified_positive_reply
    for kind in ('exact', 'positive', 'equivalent'):
        grouped = kind == 'positive'
        key = context_key(review, product, rules, facts, praise_group=grouped, equivalent_group=kind=='equivalent')
        if not key:
            continue
        row = db.get(Setting, 'verified-replies:' + key[0])
        entry = deepcopy((row.value or {}).get(key)) if row else None
        from .verified_reply_library import variants
        for item in variants(db,key,kind) + _variants(entry, db):
            if not item.get('durable') and time.time()-float(item.get('at',0)) > MAX_AGE:
                continue
            answer, verdict = item.get('answer',''), item.get('verdict') or {}
            if exact_answer is not None and answer != exact_answer:
                continue
            if (same_answer(answer, previous) or not verdict.get('checked') or not verdict.get('passed')
                    or verdict.get('technical_error') or verdict.get('issues')):
                continue
            try:
                from .equivalent_reviews import message_group
                if (grouped and not verified_positive_reply(answer,review,rules,product=product) or
                    kind=='equivalent' and message_group(review.text)=='untried' and not verified_positive_reply(answer,review,rules,product=product) or
                    validate_reply(answer) != answer or local_issues(answer,rules,review) or
                    not saved_rule_quality(answer,rules,verdict,review=review)['passed'] or not assess(answer)['passed']):
                    continue
            except ValueError:
                continue
            verdict.update(verification_method='learned_positive_group' if grouped else 'learned_equivalent_context' if kind=='equivalent' else 'learned_exact_context',
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


def similar_candidate(db, review, product, rules, facts, assess, *, previous=''):
    key = context_key(review, product, rules, facts, candidate_group=True)
    if not key:
        return None
    row = db.get(Setting, 'verified-replies:' + key[0])
    from .verified_reply_library import variants
    for item in variants(db,key,'candidate') + _variants((row.value or {}).get(key) if row else None, db):
        verdict, answer = item.get('verdict') or {}, item.get('answer', '')
        if ((item.get('durable') or time.time()-float(item.get('at',0)) <= MAX_AGE) and not same_answer(answer, previous)
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
    from .verified_reply_library import remember
    from .rule_execution import verified_positive_reply
    keys = [(key,'exact')]
    similar = context_key(review,product,rules,facts,candidate_group=True)
    if similar: keys.append((similar,'candidate'))
    equivalent = context_key(review,product,rules,facts,equivalent_group=True)
    from .equivalent_reviews import message_group
    if equivalent and (message_group(review.text)!='untried' or verified_positive_reply(draft.text,review,rules,product=product)):
        keys.append((equivalent,'equivalent'))
    if verified_positive_reply(draft.text,review,rules,product=product):
        grouped=context_key(review,product,rules,facts,praise_group=True)
        if grouped: keys.append((grouped,'positive'))
    for key,kind in keys:
        remember(db,key,kind,draft.text,verdict,source=review.id,marketplace=review.marketplace)
    return True


def backfill_equivalent_templates(db):
    """Index only durable exact proofs whose original context still matches."""
    from types import SimpleNamespace
    from .db import VerifiedReplyContext,VerifiedReplyText,Review,Product,set_setting
    from .verified_reply_library import render
    from .owner_reply_rules import matching_owner_rules
    from .owner_reply_policy import attach_rule_quality
    from .services import assess_reply,review_verified_facts
    from .equivalent_reviews import message_group,VERSION
    cursor='';indexed=0;seen=0
    while True:
        rows=list(db.scalars(select(VerifiedReplyContext).where(VerifiedReplyContext.kind=='exact',
            VerifiedReplyContext.id>cursor).order_by(VerifiedReplyContext.id).limit(300)))
        if not rows:break
        for entry in rows:
            seen+=1
            verdict=entry.verdict if isinstance(entry.verdict,dict) else {}
            if (verdict.get('checked') is not True or verdict.get('passed') is not True
                or verdict.get('technical_error') or verdict.get('issues')):continue
            review=db.get(Review,entry.source_review_id)
            if not review or not message_group(review.text):continue
            product=db.get(Product,review.product_id)
            if not product:continue
            rules=matching_owner_rules(db,product)
            facts=review_verified_facts(db,review,product,rules)
            if entry.context_key!=context_key(review,product,rules,facts):continue
            body=db.get(VerifiedReplyText,entry.template_id)
            if not body:continue
            answer=render(body.text,entry.presentation)
            quality=attach_rule_quality(assess_reply(review,product,answer,facts),verdict)
            if remember_template(db,review,product,rules,facts,SimpleNamespace(text=answer,quality=quality)):
                indexed+=1
        cursor=rows[-1].id
    set_setting(db,'equivalent_review_templates_version',VERSION)
    return {'examined':seen,'indexed':indexed,'version':VERSION}


def compact_library(db):
    from .verified_reply_library import migrate_legacy
    return migrate_legacy(db)


if __name__=='__main__':
    from .db import Session
    with Session() as db:
        result=backfill_equivalent_templates(db);db.commit();print(json.dumps(result))
