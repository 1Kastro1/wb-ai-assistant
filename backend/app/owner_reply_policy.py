"""Apply and verify owner rules for every generation route, without trusting a prompt alone."""
import asyncio
import hashlib
import json
import re
from copy import deepcopy
from functools import lru_cache
from time import monotonic, time

from .plain_text import plain_text
from .safety import mask_vin, validate_reply
from .reply_policy import has_redundant_clarification, remove_redundant_clarification, analyze_customer_message, missing_complaints, COMPLAINT_LABELS
from .rule_execution import select_rules, positive_template, audited_case_template, verified_positive_reply, verification_key, cached_verdict, remember_verdict


QUOTED = r'[«“"\u2018\u2019]([^»”"\u2018\u2019\n]{1,200})[»”"\u2018\u2019]'
GREETING = re.compile(r'(?:начина\w*|начин\w*|начало\s+ответа|приветств\w*)[^.!?«“"\n]{0,70}' + QUOTED, re.I)
BANNED = re.compile(r'(?:не\s+(?:использ\w*|пиши\w*|писать|упомина\w*|добавля\w*)|избега\w*)\s*(?:(?:слов[ао]?|фраз[уы]|выражени[ея]|формулировк[уы])\s*)?[:—-]?\s*' + QUOTED, re.I)
SIGNATURE = re.compile(r'(?:подпись\s*:?\s*|(?:заканчива\w*|заверша\w*)[^.!?«“"\n]{0,50})' + QUOTED, re.I)
REASK = re.compile(r'[^.!?]*(?:уточните|расскажите|опишите)[^.!?]*(?:что именно[^.!?]*(?:не устроило|не понравилось)|какие свойства[^.!?]*не устроили|в ч[её]м проявилась проблема)[^.!?]*[.!?]?', re.I)


def fingerprint(rules):
    return hashlib.sha256(json.dumps(rules, ensure_ascii=False).encode()).hexdigest()


def _clauses(rule):
    # A full stop or exclamation mark inside a quoted greeting is not a rule boundary.
    quotes = []
    def hold(match):
        quotes.append(match.group(0))
        return f'RULEQUOTE{len(quotes)-1}TOKEN'
    protected = re.sub(QUOTED, hold, rule)
    for clause in re.split(r'[.!?;]\s+|\n+', protected):
        for i, quote in enumerate(quotes):
            clause = clause.replace(f'RULEQUOTE{i}TOKEN', quote)
        if clause.strip(' .;'):
            yield clause.strip(' .;')


def constraints(rules):
    # This caches syntax only, never a compliance verdict or a buyer context.
    # Exact ordered rule texts are the key; callers cannot mutate shared state.
    return deepcopy(_parsed_constraints(tuple(rules)))


@lru_cache(maxsize=256)
def _parsed_constraints(rules):
    result = {'greeting': '', 'anonymous_greeting': '', 'greetings': [], 'signature': '', 'banned': [], 'no_reask': False,
              'no_emoji': False, 'no_photos': False, 'no_order_support': False, 'semantic': []}
    for rule in rules:
        for clause in _clauses(rule):
            lower = clause.lower().replace('ё', 'е')
            alternatives = re.match(r'(?:Допустимые приветствия:\s*|Приветствие:\s*|Начинай с\s*)«([^»]+)»\s+или\s+«([^»]+)»', clause, re.I)
            if alternatives:
                result['greetings'] = list(dict.fromkeys(alternatives.groups()))
                # Keep any remaining instruction for the semantic checker.
                remainder = clause[alternatives.end():].strip(' ,:')
                if remainder and remainder.lower() not in ('оба допустимы', 'оба варианта допустимы'):
                    result['semantic'].append(remainder)
                continue
            if re.search(r'не\s+(?:прос\w*|треб\w*)[^.!?]{0,30}(?:фото|видео)', lower):
                result['no_photos'] = True
            if re.search(r'не\s+направля\w*[^.!?]{0,80}через\s+(?:полученный\s+)?заказ', lower):
                result['no_order_support'] = True
            no_reask = bool(re.search(r'уже\s+(?:опис|указ|назван)|повторно\s+опис|заново\s+опис', lower)) and bool(re.search(r'не\s+(?:спраш|уточн|прос|переспраш)', lower))
            greeting = GREETING.search(clause)
            if greeting:
                condition = clause[:greeting.start()].lower().strip(' ,:')
                anonymous = re.fullmatch(r'(?:если|когда) имя (?:покупателя|пользователя) (?:скрыто или отсутствует|скрыто|отсутствует|не указано|неизвестно)', condition)
                if anonymous and not clause[greeting.end():].strip(' .!'):
                    result['anonymous_greeting'] = greeting.group(1).strip()
                    continue
            if not no_reask and re.search(r'\b(?:если|когда|при|в случае)\b|(?:позитивн|негативн|положительн|отрицательн)\w*\s+(?:отзыв|ответ)', lower):
                # Conditional style rules require a context check; they must not
                # silently become unconditional text substitutions.
                result['semantic'].append(clause)
                continue
            known = False
            greeting = GREETING.search(clause)
            if greeting and not re.search(r'не\s+(?:начина|начин|приветств)', lower):
                result['greeting'] = greeting.group(1).strip()
                known = True
            # Restrict bans to the quote immediately governed by the negative verb.
            # "Не пиши «Здравствуйте», начинай с «Добрый день!»" must not ban both.
            bans = [match.group(1).strip() for match in BANNED.finditer(clause)]
            if bans:
                result['banned'].extend(bans)
                known = True
            signature = SIGNATURE.search(clause)
            if signature and not re.search(r'не\s+(?:добавля|использ|пиши|заверша|заканчива)', lower):
                result['signature'] = signature.group(1).strip()
                known = True
            if no_reask:
                result['no_reask'] = True
                known = True
            if re.search(r'(?:без|не используй|не добавляй)\s+(?:эмодзи|смайл)', lower):
                result['no_emoji'] = True
                known = True
            # A recognised style directive must not hide an unrelated requirement
            # in the same sentence (e.g. "... и рассказывай о наличии товара").
            remainder = GREETING.sub('', clause)
            remainder = BANNED.sub('', remainder)
            remainder = SIGNATURE.sub('', remainder)
            extra = bool(re.search(r'\b(?:и|но|также)\s+(?!начина|заверша|заканчива|не\s+пиши)\w+', remainder, re.I))
            if not known or extra:
                result['semantic'].append(clause)
    return result


def semantic_rules(rules):
    """Keep whole conditional/list rules together; never orphan their conditions.

    Local style parsing may split clauses, but a semantic judge needs the title,
    condition and following list as one unit. Deduplicate without editing policy.
    """
    return list(dict.fromkeys(rule for rule in rules if constraints([rule])['semantic']))


def rule_groups(rules, max_chars=14000):
    groups, current, size = [], [], 0
    for rule in rules:
        if current and size + len(rule) > max_chars:
            groups.append(current)
            current, size = [], 0
        current.append(rule)
        size += len(rule)
    if current:
        groups.append(current)
    return groups


def context_constraints(rules, review=None):
    config = constraints(rules)
    name = str(getattr(review, 'buyer_name', '') or getattr(review, 'customer_name', '') or '').strip()
    hidden = (not name or bool(getattr(review, 'buyer_name_hidden', False)) or
              bool(re.search(r'скрыт|аноним|пользователь|покупатель|hidden|anonymous|\*{2,}', name, re.I)))
    if config['greetings']:
        config['greeting'] = config['greetings'][0]
    elif config['anonymous_greeting'] and hidden:
        config['greeting'] = config['anonymous_greeting']
    return config


def anonymous_opening(reply):
    # Remove an unsupported name only as part of the opening, not the body.
    greeting = r'(?i:здравствуйте|добрый\s+(?:день|вечер)|доброе\s+утро|приветствуем(?:\s+Вас)?)'
    reply = re.sub(r'^\s*[^\n.!?]{1,80},\s*(?=' + greeting + ')', '', reply)
    name = r'(?:[А-ЯЁA-Z][а-яёa-z-]+(?:\s+[А-ЯЁA-Z][а-яёa-z-]+){0,2}|(?i:имя скрыто|пользователь(?: ozon)?|покупатель|аноним))'
    reply = re.sub(r'^(\s*' + greeting + r'),\s*' + name + r'[!.,:]\s*', r'\1! ', reply)
    return re.sub(r'^\s*' + greeting + r'[!.,:\s]*', '', reply)


def apply_local_preferences(reply, review, rules, style_only=False):
    config = context_constraints(rules, review)
    from .reply_identity import normalize_reply_opening
    reply = normalize_reply_opening(plain_text(reply), review)
    applied = []
    if config['no_reask'] and review.text.strip() and not style_only:
        reply = REASK.sub('', reply)
        reply = remove_redundant_clarification(reply, review.text)
        applied.append('не переспрашивать описанную проблему')
    for phrase in ([] if style_only else config['banned']):
        # Whole phrases, not fragments inside another word or an OEM number.
        reply = re.sub(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', '', reply, flags=re.I)
        applied.append('запрет слова: ' + phrase)
    if config['no_emoji']:
        reply = re.sub('[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]', '', reply)
        applied.append('без эмодзи')
    if not style_only:
        sentences = re.split(r'(?<=[.!?])\s+', reply)
        reply = ' '.join(sentence for sentence in sentences if not (
            config['no_photos'] and re.search(r'фотограф|пришлите фото|снимок|видео', sentence, re.I)
            or config['no_order_support'] and re.search(r'через\s+(?:полученный\s+)?заказ', sentence, re.I)))
    greeting = config['greeting']
    if greeting and not any(reply.startswith(value) for value in config['greetings']):
        if config['anonymous_greeting'] and greeting == config['anonymous_greeting']:
            reply = anonymous_opening(reply)
        reply = reply.lstrip(' \t\r\n!.,:;')
        reply = re.sub(r'^\s*(?:здравствуйте|добрый\s+(?:день|вечер)|доброе\s+утро|приветствуем(?:\s+Вас)?)[!.,:\s]*', '', reply, flags=re.I)
        if not reply.casefold().startswith(greeting.casefold()):
            reply = greeting + (' ' + reply.lstrip() if reply.strip() else '')
        applied.append('приветствие')
    return re.sub(r'[ \t]{2,}', ' ', reply).strip(), list(dict.fromkeys(applied))


def apply_signature(reply, rules):
    signature = constraints(rules)['signature']
    if not signature:
        return reply
    body = re.sub(r'(?:\s*' + re.escape(signature) + r')+\s*$', '', reply, flags=re.I).rstrip()
    return body + '\n' + signature if body else signature


def local_issues(reply, rules, review=None):
    config = context_constraints(rules, review)
    issues = []
    from .reply_identity import normalize_reply_opening
    if review is not None and normalize_reply_opening(reply, review) != reply:
        issues.append('Не использовать неподтверждённое имя покупателя')
    if config['greeting'] and not any(reply.startswith(value) for value in (config['greetings'] or [config['greeting']])):
        issues.append('Не выполнено начало ответа: ' + config['greeting'])
    if config['signature'] and not reply.endswith(config['signature']):
        issues.append('Не выполнена подпись ответа')
    for phrase in config['banned']:
        if re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', reply, re.I):
            issues.append('В ответе запрещённая фраза: ' + phrase)
    if config['no_photos'] and re.search(r'фотограф|пришлите фото|снимок|видео', reply, re.I):
        issues.append('Не просить фотографии или видео')
    if config['no_order_support'] and re.search(r'через\s+(?:полученный\s+)?заказ', reply, re.I):
        issues.append('Не направлять через полученный заказ')
    if config['no_reask'] and review is not None and has_redundant_clarification(reply, review.text):
        issues.append('Не переспрашивать уже описанную проблему')
    if any(re.search(r'одн[ау]\s+благодарность|не\s+повторяй\s+благодарность', rule, re.I) for rule in rules):
        if len(re.findall(r'\b(?:спасибо|благодарим|благодарю|благодарны)\b', reply, re.I)) > 1:
            issues.append('Использовать одну благодарность без повторов')
    if rules and review is not None and not (getattr(review, 'marketplace', '') or '').endswith('_question'):
        analysis = analyze_customer_message(review.text, getattr(review, 'rating', None))
        for topic in missing_complaints(analysis, reply):
            issues.append('Не учтено замечание покупателя: ' + COMPLAINT_LABELS.get(topic, topic))
    return issues


CHECK_SCHEMA = {'type': 'object', 'properties': {
    'compliant': {'type': 'boolean'},
    'violations': {'type': 'array', 'items': {'type': 'string'}},
    'revised_answer': {'type': 'string'}},
    'required': ['compliant', 'violations', 'revised_answer'], 'additionalProperties': False}


async def enforce_owner_rules(reply, review, product, rules, facts, assess, *, protected=False, check_seconds=None,
                              allow_fast_positive=False, instruction='', local_only=False, check_only=False,
                              defer_semantic=False, previous_check=None, group_limit=None):
    """Return text and an honest verification result, including outages/conflicts.

    Catalog and critical safety conclusions may be checked but never rewritten by
    a style model. No rule can turn itself into verified product/catalog evidence.
    """
    from .integrations import ollama
    check_started = monotonic()
    adjusted, applied = (reply, []) if check_only else apply_local_preferences(reply, review, rules, style_only=protected)
    if not check_only:
        adjusted = apply_signature(adjusted, rules)
    result = {'matched': len(rules), 'applied_preferences': applied,
              'fingerprint': fingerprint(rules), 'checked': True, 'passed': True, 'issues': []}
    try:
        adjusted = validate_reply(mask_vin(adjusted))
    except ValueError as error:
        result.update(passed=False, issues=['Правило конфликтует с проверкой ответа: ' + str(error)])
        return validate_reply(mask_vin(reply)), result
    result['issues'] = local_issues(adjusted, rules, review)
    context_rules, skipped = select_rules(rules, review, product)
    result.update(context_rules=len(context_rules), skipped_rules=skipped, cache_hit=False)
    semantic = semantic_rules(context_rules)
    key = None
    if not instruction.strip() and not protected and not result['issues']:
        from .brake_feedback import template as brake_template
        expected = brake_template(review, product, rules)
        if expected and adjusted == expected and assess(adjusted)['passed']:
            result.update(verification_method='local_brake_feedback', semantic_groups=0, verified_groups=0,
                          verification_seconds=round(monotonic() - check_started, 4))
            return adjusted, result
    if not instruction.strip() and not protected and not result['issues']:
        from .rule_execution import audited_usage_replies
        if adjusted in audited_usage_replies(review, product, rules, facts) and assess(adjusted)['passed']:
            result.update(verification_method='local_verified_usage', semantic_groups=0, verified_groups=0,
                          verification_seconds=round(monotonic() - check_started, 4))
            return adjusted, result
    if not instruction.strip() and not protected and not result['issues']:
        expected = audited_case_template(review, product, rules)
        if expected and adjusted == expected and assess(adjusted)['passed']:
            result.update(verification_method='local_review_case', semantic_groups=0, verified_groups=0,
                          verification_seconds=round(monotonic() - check_started, 4))
            return adjusted, result
    if check_only and not instruction.strip() and not protected and not result['issues']:
        if verified_positive_reply(adjusted, review, rules, product=product) and assess(adjusted)['passed']:
            method = 'local_positive' if any(adjusted == positive_template(review, rules, i, product=product) for i in range(3)) else 'local_saved_positive'
            result.update(verification_method=method, semantic_groups=0, verified_groups=0,
                          verification_seconds=round(monotonic() - check_started, 4))
            return adjusted, result
    # Only the exact no-claims templates and audited whole policies qualify.
    # Unknown/edited semantic rules must still reach the model, even on five stars.
    if allow_fast_positive and not instruction.strip() and not protected and not result['issues']:
        if any(adjusted == positive_template(review, rules, revision, product=product) for revision in range(3)) and assess(adjusted)['passed']:
            result.update(verification_method='local_positive', semantic_groups=0, verified_groups=0,
                          verification_seconds=round(monotonic() - check_started, 4))
            return adjusted, result
    if local_only:
        from .generation_control import FastPathUnavailable
        raise FastPathUnavailable('Нужна дополнительная проверка правил')
    if semantic and not protected and not result['issues']:
        from .model_router import ModelRouter
        key = verification_key(adjusted, review, product, rules, facts, instruction, ModelRouter().choose('OWNER_RULES'))
        cached = cached_verdict(key)
        if cached:
            value, verdict = cached
            if (not check_only or value == reply) and validate_reply(mask_vin(value)) == value and not local_issues(value, rules, review) and assess(value)['passed']:
                verdict.update(cache_hit=True, verification_method='exact_input_cache',
                               reused_check_seconds=verdict.get('verification_seconds', 0),
                               verification_seconds=round(monotonic() - check_started, 4))
                return value, verdict
    if semantic and protected:
        result.update(checked=False)
        result['issues'].append('Проверьте дополнительные правила магазина: вывод каталога или предупреждение безопасности сохранены без переформулировки.')
    elif semantic and defer_semantic:
        # Generation has already observed an unavailable model. Save the draft
        # and retry verification durably rather than spending a second deadline.
        result.update(checked=False, technical_error=True, reason='сервис модели недоступен')
        result['issues'].append('Ответ сохранён; проверка правил продолжится в фоне после восстановления сервиса модели.')
    elif semantic:
        # A model repair may resolve the initial local defects. Recompute them
        # against the final text below instead of retaining stale warnings.
        result['issues'] = []
        groups = rule_groups(semantic)
        result['semantic_groups'] = len(groups)
        try:
            # One deadline for the check, optional repair and recheck together.
            from .generation_budget import GenerationBudget
            from .generation_budget import OWNER_RULE_CHECK_SECONDS
            budget = GenerationBudget(OWNER_RULE_CHECK_SECONDS if check_seconds is None else check_seconds)
            for attempt in range(2):
                from .rule_check_plan import prepare, request_json, step_deadline
                messages, payload, schema, groups = prepare(review, product, semantic, facts, adjusted,
                    protected=protected, check_only=check_only, style_rules=rules)
                result['semantic_groups'] = len(groups)
                # Persist successful groups bound to every input and partition.
                # Partial progress is never approval; all groups must pass.
                from .model_router import ModelRouter
                progress_key = verification_key(adjusted, review, product, rules, facts, instruction,
                    ModelRouter().choose('OWNER_RULES'))
                partition = hashlib.sha256(json.dumps([groups, messages[0], schema, payload], ensure_ascii=False).encode()).hexdigest()
                prior = (previous_check or {}).get('group_progress') or {}
                resume = (attempt == 0 and prior.get('version') == 1
                    and prior.get('input') == progress_key and prior.get('partition') == partition
                    and isinstance(prior.get('expires_at'), (int, float)) and prior['expires_at'] > time())
                completed = set(prior.get('completed', [])) if resume else set()
                completed = {i for i in completed if type(i) is int and 0 <= i < len(groups)}
                progress = {'version': 1, 'input': progress_key, 'partition': partition,
                    'expires_at': prior['expires_at'] if resume else time() + 86400,
                    'completed': sorted(completed)}
                result['group_progress'] = progress
                result['verified_groups'] = len(completed)
                result['resumed_groups'] = len(completed)
                performed = 0
                for index, group in enumerate(groups):
                    if index in completed:
                        continue
                    messages[-1]['content'] = request_json(payload, group)
                    durable = bool(check_only and group_limit)
                    seconds = step_deadline(messages, schema, check_seconds or OWNER_RULE_CHECK_SECONDS) if durable else check_seconds
                    step_budget = GenerationBudget(seconds) if durable else budget
                    result['planned_timeout_seconds'] = seconds or OWNER_RULE_CHECK_SECONDS
                    check = await step_budget.run(lambda: ollama.structured(messages, schema, task='OWNER_RULES',
                        **({'timeout_seconds': seconds} if seconds is not None else {})))
                    if (not isinstance(check, dict) or type(check.get('compliant')) is not bool
                        or not isinstance(check.get('violations'), list)
                        or any(not isinstance(item, str) for item in check['violations'])
                        or not isinstance(check.get('revised_answer'), str)):
                        raise ValueError('Некорректный результат проверки правил')
                    if not check['compliant'] and not check['violations']:
                        raise ValueError('Проверка не указала нарушенное правило')
                    if not check['compliant'] or check['violations']:
                        break
                    completed.add(index)
                    progress['completed'] = sorted(completed)
                    result['verified_groups'] = len(completed)
                    performed += 1
                    if durable and performed >= group_limit and len(completed) < len(groups):
                        result.update(checked=False, continuation=True, retryable=True, technical_error=False)
                        result['issues'].append(f'Проверено этапов: {len(completed)} из {len(groups)}. Проверка продолжается.')
                        break
                if result.get('continuation'):
                    break
                if result['verified_groups'] == len(groups):
                    break
                candidate = check['revised_answer'].strip()
                if not candidate or protected or attempt or check_only:
                    result['issues'].extend(check['violations'] or ['Правила магазина не выполнены'])
                    break
                candidate, _ = apply_local_preferences(candidate, review, rules)
                candidate = validate_reply(mask_vin(apply_signature(candidate, rules)))
                if re.sub(r'\s+', ' ', candidate).strip() == re.sub(r'\s+', ' ', adjusted).strip():
                    # The verifier has not repaired anything. A second identical
                    # request cannot prove compliance with its own violation.
                    result['issues'].extend(check['violations'])
                    break
                # Rewriting must not silently discard verified instructions or
                # introduce new model claims accepted only by the rule checker.
                quality = assess(candidate)
                if not quality['passed'] or _new_numbers(candidate, adjusted, facts):
                    result['issues'].extend(check['violations'] or ['Исправление правила не прошло проверку фактов'])
                    break
                adjusted = candidate
                applied.append('исправление по правилам магазина')
        except Exception as error:
            from .rule_retry import verification_failure
            failure = verification_failure(error)
            result.update(checked=False, technical_error=failure['retryable'], **failure)
            result['issues'].append('Ответ сохранён. Проверка правил продолжится автоматически после восстановления сервиса.'
                if failure['retryable'] else 'Ответ сохранён. Не удалось проверить правила: ' + failure['reason'])
    result['issues'] = list(dict.fromkeys(result['issues'] + local_issues(adjusted, rules, review)))
    result['violations'] = local_issues(adjusted, rules, review) if result.get('checked') is False else list(result['issues'])
    result['passed'] = not result['issues']
    result['applied_preferences'] = list(dict.fromkeys(applied))
    result['verification_method'] = 'model' if semantic else 'local_constraints'
    result['verification_seconds'] = round(monotonic() - check_started, 4)
    if key and result['passed'] and assess(adjusted)['passed']:
        remember_verdict(key, adjusted, result)
    return adjusted, result


def _new_numbers(candidate, original, facts):
    known = original + ' ' + ' '.join(str(fact.get('text', '')) for fact in facts)
    return set(re.findall(r'\d+(?:[.,]\d+)?', candidate)) - set(re.findall(r'\d+(?:[.,]\d+)?', known))


def attach_rule_quality(quality, rule_quality):
    # Keep content defects separate from verification availability. A timeout
    # must never be displayed as evidence of a text violation, or as approval.
    previous = quality.get('owner_rules') or {}
    text_issues = quality.get('text_issues')
    if text_issues is None:
        text_issues = [issue for issue in quality.get('issues', []) if issue not in previous.get('issues', [])]
    quality['text_issues'] = list(text_issues)
    rule_quality = dict(rule_quality)
    if (quality.get('passed') is True and rule_quality.get('passed') is True
            and rule_quality.get('checked') is True and not rule_quality.get('technical_error')
            and not rule_quality.get('issues') and not rule_quality.get('violations')):
        # A new draft can reuse a proven template. Record this validated save
        # event so publication sees it independently of the archive-date cursor.
        from .db import now
        rule_quality['checked_at'] = now()
    rule_quality['status'] = ('verified' if rule_quality.get('passed') else
                              'blocked' if rule_quality.get('retryable') is False else
                              'error' if rule_quality.get('technical_error') else
                              'pending' if rule_quality.get('checked') is False else 'violation')
    quality['owner_rules'] = rule_quality
    quality['issues'] = list(dict.fromkeys([*text_issues, *rule_quality.get('issues', [])]))
    if not rule_quality['passed']:
        quality['passed'] = False
        quality['issues'] = list(dict.fromkeys([*quality.get('issues', []), *rule_quality['issues']]))
    return quality


def saved_rule_quality(reply, rules, previous=None, *, changed=False, review=None):
    """A saved/manual draft cannot inherit a rule check for different text/rules."""
    previous = previous or {}
    issues = local_issues(reply, rules, review)
    violations = list(issues)
    current = fingerprint(rules)
    if review is not None and not issues and verified_positive_reply(reply, review, rules):
        return {'matched': len(rules), 'fingerprint': current, 'checked': True, 'passed': True,
                'issues': [], 'violations': [], 'status': 'verified',
                'verification_method': 'local_saved_positive', 'verification_seconds': 0}
    if rules and (changed or previous.get('fingerprint') != current) and constraints(rules)['semantic']:
        issues.append('Ответ ожидает проверки по текущим правилам магазина.')
        previous = {key: value for key, value in previous.items() if key not in ('technical_error', 'retryable', 'failure_code', 'reason', 'verification_seconds', 'verification_method', 'status')}
    elif rules and previous.get('fingerprint') == current and not changed and not previous.get('passed', True):
        issues.extend(previous.get('issues') or ['Выполнение правил магазина не подтверждено. Подготовьте ответ заново.'])
    return {**previous, 'matched': len(rules), 'fingerprint': current,
            'checked': not issues, 'passed': not issues, 'issues': list(dict.fromkeys(issues)),
            'violations': list(dict.fromkeys(violations + ([] if changed else previous.get('violations', []))))}


