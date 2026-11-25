"""Deterministic confirmation and bounded repair of demonstrable reply defects."""
import re

from .owner_reply_policy import attach_rule_quality, enforce_owner_rules
from .safety import mask_vin, validate_reply

RATING = 'Ответ ошибочно благодарит за высокую оценку при оценке 1–3★.'
ADAPTER = 'Ответ не упоминает крепление, о котором спросил покупатель.'
BLOCKED = 'Сначала исправьте подтверждённую ошибку в ответе.'


def normalized(text):
    return re.sub(r'\s+', ' ', (text or '').casefold().replace('ё', 'е'))


def adapter_code(question):
    match = re.search(r'\bпереходник\w*\s+(?:на|для)\s+(valt\s*\d+(?:[.,]\d+)?)\b',
                      normalized(question.text))
    return re.sub(r'\s+', '', match[1]).replace(',', '.') if match else ''


def mentions_adapter(text, code):
    number = re.escape(code.removeprefix('valt')).replace(r'\.', '[.,]')
    return bool(re.search(rf'\bvalt\s*{number}\b(?![.,]\d)', normalized(text)))


def confirmed_issue(review, reply):
    answer = normalized(reply)
    if not review.marketplace.endswith('_question'):
        return RATING if 1 <= review.rating <= 3 and re.search(r'за (?:вашу )?высокую оценку', answer) else ''
    code = adapter_code(review)
    if (code and not mentions_adapter(answer, code)
            and re.search(r'недостаточно подтвержд|не хотим вводить', answer)):
        return ADAPTER
    return ''


def replacement(review, product, issue, facts):
    if issue == ADAPTER:
        code = adapter_code(review)
        if any(mentions_adapter(fact.get('text'), code) for fact in facts):
            return ''
        label = f'Valt {code.removeprefix("valt")}'
        vehicle = ' для Весты' if re.search(r'\bвест[аыуе]\b', normalized(review.text)) else ''
        return (f'Добрый день! Спасибо за вопрос. По данным о комплектации не можем подтвердить, '
                f'что к этой щётке есть переходник {label}{vehicle}.')
    buyer = normalized(review.text)
    if ('стеклоочист' in normalized(product.name) and
            re.search(r'спустя\s+(?:2|две)\s+недел\w*\s+щетк\w*\s+трет', buyer)):
        return ('Добрый день! Спасибо, что рассказали, как работает щётка. '
                'Жаль, что уже через две недели она стала плохо очищать стекло. '
                'По отзыву нельзя точно определить причину.')
    from .safety import safe_fallback_reply
    return safe_fallback_reply(review, product, facts=facts)


async def repair(review, product, reply, quality, rules, facts, assess, *, protected=False):
    """Check a new exact text. Never carry forward the old owner's rule verdict."""
    issue = confirmed_issue(review, reply)
    if not issue:
        return reply, quality
    audit = {'issue': issue, 'status': 'blocked'}
    if not protected:
        try:
            candidate = replacement(review, product, issue, facts)
            if not candidate:
                raise ValueError('Нужен ответ по конкретным сведениям о комплектации')
            # Apply house style, then check the frozen text. No model rewrite of
            # the repair, and no inherited approval from the original answer.
            from .owner_reply_policy import apply_local_preferences, apply_signature
            candidate, _ = apply_local_preferences(candidate, review, rules)
            candidate = validate_reply(mask_vin(apply_signature(candidate, rules)))
            content = assess(candidate)
            if content.get('passed') is True and not confirmed_issue(review, candidate):
                checked, verdict = await enforce_owner_rules(
                    candidate, review, product, rules, facts, assess, check_only=True)
                if checked == candidate:
                    checked_quality = attach_rule_quality(content, verdict)
                    if (checked_quality.get('passed') is True and verdict.get('checked') is True
                            and checked_quality['owner_rules'].get('checked_at')):
                        checked_quality['confirmed_repair'] = {**audit, 'status': 'rechecked'}
                        return checked, checked_quality
        except (ValueError, TimeoutError):
            pass
    return reply, {**quality, 'passed': False, 'confirmed_repair': audit,
                   'text_issues': list(dict.fromkeys([*(quality.get('text_issues') or []), issue])),
                   'issues': list(dict.fromkeys([*(quality.get('issues') or []), issue]))}
