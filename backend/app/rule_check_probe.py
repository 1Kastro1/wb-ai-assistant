"""Read-only request probe; never saves a reply or records an approval."""
import argparse
import asyncio
import json
import time
from sqlalchemy import select
from .db import Session, Review, Product, Draft
from .owner_reply_rules import matching_owner_rules
from .owner_reply_policy import semantic_rules
from .rule_execution import select_rules
from .services import review_verified_facts
from .rule_check_plan import prepare, request_json, step_deadline
from .model_context import request_tokens
from .integrations import ollama, guard
from .generation_budget import GenerationBudget


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('review_id')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--group', type=int, default=0)
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--threads', type=int, choices=range(1,65))
    args = parser.parse_args()
    with Session() as db:
        review = db.get(Review, args.review_id)
        if not review: raise ValueError('Review not found')
        product = db.get(Product, review.product_id)
        draft = db.scalar(select(Draft).where(Draft.review_id == review.id))
        if not product or not draft: raise ValueError('Product or saved draft missing')
        rules = matching_owner_rules(db, product)
        facts = review_verified_facts(db, review, product, rules)
        semantic = semantic_rules(select_rules(rules, review, product)[0])
        messages, payload, schema, groups = prepare(review, product, semantic, facts, draft.text, check_only=True)
    print(json.dumps({'groups': len(groups), 'facts': len(facts), 'fact_chars': sum(len(f.get('text','')) for f in facts)}, ensure_ascii=False), flush=True)
    for index, group in enumerate(groups):
        messages[-1]['content'] = request_json(payload, group)
        print(json.dumps({'group': index, 'rules':len(group), 'estimated_tokens': request_tokens(messages,schema),
            'deadline':step_deadline(messages,schema,180)}), flush=True)
    if not args.run: return
    if not 0 <= args.group < len(groups): raise ValueError('Unknown group')
    messages[-1]['content'] = request_json(payload, groups[args.group])
    original = guard.request
    async def measured(*params, **kwargs):
        body = kwargs.get('body') or {}
        if args.threads:
            body['options']['num_thread'] = args.threads
        started = time.monotonic()
        print(json.dumps({'request_started':True,'context':body.get('options',{}).get('num_ctx'),
            'timeout':kwargs.get('timeout_seconds'),'threads':body.get('options',{}).get('num_thread')}),flush=True)
        response = await original(*params, **kwargs)
        print(json.dumps({'response_seconds':round(time.monotonic()-started,3),
            **{key:response.get(key) for key in ('prompt_eval_count','eval_count','prompt_eval_duration','eval_duration','load_duration','done_reason')}}),flush=True)
        return response
    guard.request = measured
    async def run():
        return await GenerationBudget(min(600,args.seconds)).run(lambda:ollama.structured(messages,schema,task='OWNER_RULES',timeout_seconds=min(600,args.seconds)))
    result = asyncio.run(run())
    print(json.dumps({'probe_only':True,'compliant':result.get('compliant'), 'violations':result.get('violations')} ,ensure_ascii=False),flush=True)


if __name__ == '__main__': main()
