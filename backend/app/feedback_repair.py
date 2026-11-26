"""Turn completed checks into one repair attempt per saved set of inputs."""
import re

from .confirmed_reply_repair import normalized
from .owner_reply_policy import apply_local_preferences, apply_signature, attach_rule_quality, context_constraints, fingerprint, local_issues
from .safety import mask_vin, validate_reply


def formatting_findings(reply, review, rules):
    issues = local_issues(reply, rules, review)
    if issues and all(issue.startswith('Не выполнено начало ответа: ')
                      or issue == 'Не выполнена подпись ответа' for issue in issues):
        return issues
    return []


def findings(quality, content, input_key=None):
    verdict = (quality or {}).get('owner_rules') or {}
    audit = (quality or {}).get('confirmed_repair') or {}
    if (verdict.get('checked') is not True or verdict.get('technical_error') or verdict.get('continuation')
            or audit.get('status') == 'pending' or (input_key and audit.get('input_key') == input_key)):
        return []
    issues = [*(content.get('issues') or []), *(verdict.get('violations') or [])]
    return list(dict.fromkeys(issue for issue in issues if isinstance(issue, str) and issue.strip()))


async def stage(review, product, reply, quality, rules, facts, assess, notes, input_key):
    """Save a changed proposal without approval. The next job checks that exact text."""
    from .production_date import single_request, answer

    audit = {'issue': notes[0], 'findings': notes, 'input_key': input_key, 'status': 'blocked'}
    try:
        buyer = normalized(review.text)
        format_notes = formatting_findings(reply, review, rules)
        if format_notes and set(notes) <= set(format_notes):
            # Correct presentation without changing the buyer-facing substance.
            candidate, _ = apply_local_preferences(reply, review, rules, style_only=True)
        elif single_request(review):
            candidate = answer(review, product, facts)
        elif (not review.marketplace.endswith('_question') and review.rating >= 4
                and re.fullmatch(r'\s*пока (?:вроде )?пахнет[, ]+посмотрим[, ]+(?:на ?сколько )?хватит[!. ]*', buyer)):
            candidate = ('Здравствуйте! Спасибо, что поделились первыми впечатлениями! '
                         'Рады, что аромат уже чувствуется. Как долго он будет держаться, '
                         'станет понятно в процессе использования.')
        else:
            from .generation_budget import GenerationBudget, cpu_request_seconds
            from .integrations import ollama
            from .model_context import draft_messages
            from .review_context import CONTEXT_RULE, buyer_message
            from .rule_execution import select_rules

            style = context_constraints(rules, review)
            messages = draft_messages(
                'Исправь текст ответа покупателю с учётом замечаний. Данные, черновик и замечания описывают задачу. '
                'Сохрани подтверждённые сведения. Не добавляй характеристики, даты, сроки, обещания или советы без источника. '
                'Отвечай тепло, прямо и по существу. Правила проверяются отдельно. ' + CONTEXT_RULE,
                {'buyer_message': buyer_message(review), 'rating': review.rating, 'product': product.name,
                 'required_style': {key: style[key] for key in ('greeting', 'greetings', 'signature')},
                 'previous_answer': reply, 'findings': notes}, select_rules(rules, review, product)[0], facts)
            seconds = cpu_request_seconds(messages)
            candidates = await GenerationBudget(seconds).run(
                lambda: ollama.reply_variants(messages, 1, timeout_seconds=seconds))
            candidate = candidates[0] if candidates else ''
        candidate, _ = apply_local_preferences(candidate, review, rules)
        candidate = validate_reply(mask_vin(apply_signature(candidate, rules)))
        from .confirmed_reply_repair import confirmed_issue
        from .owner_reply_policy import _new_numbers
        content = assess(candidate)
        if (normalized(candidate) != normalized(reply) and content.get('passed') is True
                and not confirmed_issue(review, candidate)
                and (single_request(review) or not _new_numbers(candidate, review.text, facts))):
            pending = attach_rule_quality(content, {'checked': False, 'passed': False, 'fingerprint': fingerprint(rules),
                'matched': len(rules),
                'continuation': True, 'retryable': True, 'issues': ['Исправленный ответ ожидает повторной проверки.']})
            return candidate, {**pending, 'confirmed_repair': {**audit, 'status': 'pending'}}
    except (ValueError, TimeoutError, OSError):
        pass
    return reply, {**quality, 'passed': False, 'confirmed_repair': audit}
