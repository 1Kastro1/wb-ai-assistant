"""Prepare a replacement under current owner rules; never approve by default."""
VERSION = 'automatic-review-repair-2026-11-12-v1'


async def repair(review, product, rules, facts, assess):
    from .rule_execution import positive_template, untried_template
    from .review_cases import specific_review_case
    from .safety import safe_fallback_reply
    from .owner_reply_policy import enforce_owner_rules, attach_rule_quality
    from .generation_budget import AUTO_RULE_CHECK_SECONDS
    # Critical technical conclusions stay protected. This routine does not
    # clear risk flags, publication restrictions, or human revisions.
    if review.risk != 'NORMAL' or review.safety_critical:
        return None
    case = specific_review_case(review, product)
    candidate = (positive_template(review, rules, product=product)
                 or untried_template(review, rules, product=product)
                 or (case or {}).get('answer')
                 or safe_fallback_reply(review, product, facts=facts))
    answer, verdict = await enforce_owner_rules(candidate, review, product, rules, facts, assess,
        allow_fast_positive=True, check_seconds=AUTO_RULE_CHECK_SECONDS)
    quality = attach_rule_quality(assess(answer), verdict)
    quality['automatic_repair'] = {'version': VERSION, 'case': (case or {}).get('key', 'contextual_reply')}
    return answer, quality
