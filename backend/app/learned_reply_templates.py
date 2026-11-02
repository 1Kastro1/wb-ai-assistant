"""Bounded durable reuse of successful checks in an equivalent buyer context.

A similar-looking keyword is not a match. Rules, product, verified facts, rating,
whole buyer text and name visibility must all match; changed policy opts out.
"""
from copy import deepcopy
import hashlib
import json
import re
import time
from sqlalchemy import select
from .db import Setting, IS_SQLITE

BUCKET_LIMIT = 64
MAX_AGE = 14 * 86400


def context_key(review, product, rules, facts):
    from .rule_execution import VERSION
    from .shared_marketplaces import POLICY_VERSION
    if (not review or not product or review.marketplace.endswith('_question') or
        review.manual or review.safety_critical or review.risk != 'NORMAL'):
        return None
    def fields(obj, names):
        return {name: getattr(obj, name, None) for name in names}
    data = {'version': VERSION, 'policy_version': POLICY_VERSION, 'rules': list(rules), 'facts': facts,
        'review': fields(review, ('wb_account_id','marketplace','rating','risk','manual','safety_critical',
                                  'buyer_name','customer_name','buyer_name_hidden','topics')),
        'message': re.sub(r'\s+', ' ', review.text).strip(),
        'product': fields(product, ('id','name','brand','category','part_number','facts','marketplace'))}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def load_template(db, review, product, rules, facts, assess):
    from .owner_reply_policy import saved_rule_quality, local_issues
    from .safety import validate_reply
    key = context_key(review, product, rules, facts)
    if not key:
        return None
    row = db.get(Setting, 'verified-replies:' + key[0])
    item = deepcopy((row.value or {}).get(key)) if row else None
    if not item or time.time()-float(item.get('at',0)) > MAX_AGE:
        return None
    answer, verdict = item.get('answer',''), item.get('verdict') or {}
    if not verdict.get('checked') or not verdict.get('passed') or verdict.get('technical_error'):
        return None
    try:
        if (validate_reply(answer) != answer or local_issues(answer,rules,review) or
            not saved_rule_quality(answer,rules,verdict,review=review)['passed'] or not assess(answer)['passed']):
            return None
    except ValueError:
        return None
    verdict.update(verification_method='learned_exact_context', template_source=item.get('source',''),
                   verification_seconds=0, template_key=key)
    return answer, verdict


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
    bucket = 'verified-replies:' + key[0]
    insert = sqlite_insert if IS_SQLITE else pg_insert
    db.execute(insert(Setting).values(key=bucket,value={}).on_conflict_do_nothing(index_elements=['key']))
    row = db.scalar(select(Setting).where(Setting.key==bucket).with_for_update().execution_options(populate_existing=True))
    values = dict(row.value or {})
    values[key] = {'answer':draft.text,'verdict':deepcopy(verdict),'source':draft.id,'at':time.time()}
    row.value = dict(sorted(values.items(),key=lambda item:item[1]['at'],reverse=True)[:BUCKET_LIMIT])
    return True

