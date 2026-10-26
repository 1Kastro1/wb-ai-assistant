"""On-demand explanation of the current policy and its saved verification."""
from .owner_reply_rules import matching_owner_rules
from .owner_reply_policy import saved_rule_quality
from .rule_execution import digest, select_rules


def draft_rule_report(db, draft, review, product):
    rules = matching_owner_rules(db, product, for_questions=review.marketplace.endswith('_question'))
    verdict = saved_rule_quality(draft.text, rules, (draft.quality or {}).get('owner_rules'), review=review)
    _, skipped = select_rules(rules, review, product)
    exclusions = {row['rule_hash']: row['reason'] for row in skipped}
    verified = bool(verdict.get('passed') and verdict.get('checked'))
    return {'passed': verified, 'method': verdict.get('verification_method', ''),
            'matched': len(rules), 'issues': verdict.get('issues', []),
            'rules': [{'text': rule, 'status': 'not_applicable' if digest(rule) in exclusions else
                       'verified' if verified else 'pending',
                       'reason': exclusions.get(digest(rule), 'Выполнение подтверждено для сохранённого ответа' if verified
                                                else 'Выполнение по текущим правилам ещё не подтверждено')}
                      for rule in rules]}
