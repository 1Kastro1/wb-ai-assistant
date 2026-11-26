"""Checked templates for single-purpose questions with unambiguous inputs."""
import re

from .rule_execution import digest, select_rules
from .safety import validate_reply

METHOD = 'local_question_template'
STALE = 'Сведения или правила для шаблона изменились. Перепроверьте ответ.'

# Whole, reviewed question rules. Changed texts must use ordinary verification.
GENERAL_RULES = {
    'bc3a074280fa6e1971a7c726b35bcdeaa1b039114587ed520e44e5c209fc9a03',  # Facts and authenticity
    'b9ad2f1d5e274f3ae6cc77e956944d3ea858339944c50d5b5a2a7f26126e86ba',  # Delivery and stock
    '4dfa25e8732faeb041b57049142d0e142fcaf6ac0a87d1393984b405bf239364',  # Package quantity and markings
    '93af82cf5218f3ff53d8de50b3c88591e6388bd00bb7710eeeb2ab152c7f6d50',  # Greeting and completeness
    'e607e2034c7488c65cf485cba51355fb0c9876f61904163ec4e6f674af5bbc8b',  # Evidence and safety
    '1ea72cffe4dda72bcba9104e5e200f9b9628e12bbd3b48d3c023324787c7ab35',  # Conditional compatibility
}
DELIVERY_RULES = GENERAL_RULES | {
    # The exact product-category texts below apply to product advice or selection.
    '6ae94c76a308d4d6405d33d35e3893cf48036d85edfc8ba11992b909b3079417',
    'f249254a2e3b0bd1c6316795f8d04d247bb77c946c083887500169ac7c17e98c',
    'b5e2b7586ce1d057eb9009c47280bb659042ef48d7ef0568072c88ce27dd5cad',
    '33a138fbfb48ffd9681035b007022f00000b0128c7df7a94d397be353d3845d0',
    'c5d89beb37670f525cfcb78ac23d3b83a07324e8bc9230a1427ec08edd7011fb',
    'd06bb227af073b7fa6e1b5b8e927f236b5805c9bb477520d0652021dce1f6989',
    '56848bd1627d867e928cbfdf0811cc307fe382c6ebaa94945fb0ab0b16d44e69',
    '07fc04e9e8f876385a12fcd077627d7952753db87d4f88fed7a7e914fb0dc138',
    '033cd57dc7675f49a118ac952c26a557c38c2e002112311722ff82a00c9b5afb',
    '1a66b9ca617c835bc691d4f7e95e56b5e59ba42bac483b7a7db78a86185e66a3',
    'e1bf42723d34cd314bebd8c7dea523b2a3c9a3e2ee30de1eed72eedeae83bb41',
    '28fd4116f9c9a1dae908aead66768d32c38d7e9619a92263e73c043dcc1bd4fc',
}


def question_template(question, product, rules, facts):
    from .owner_reply_policy import apply_local_preferences, apply_signature, constraints, local_issues

    if (not question.marketplace.endswith('_question') or getattr(question, 'risk', 'NORMAL') != 'NORMAL'
            or getattr(question, 'manual', False) or getattr(question, 'safety_critical', False)):
        return None
    message = ' '.join(question.text.casefold().replace('ё', 'е').split())
    message = re.sub(r'^(?:добрый день|добрый вечер|доброе утро|здравствуйте)[!.,]?\s+', '', message)
    message = re.sub(r'^подскажите(?:, пожалуйста)?,?\s+', '', message).rstrip('?.! ')
    allowed = DELIVERY_RULES
    if re.fullmatch(r'(?:(?:есть(?: ли)? )?(?:этот )?товар |есть(?: ли)? )?в наличии', message):
        body = 'Чтобы узнать, есть ли товар в наличии, посмотрите его карточку перед оформлением заказа.'
    elif re.fullmatch(r'(?:когда )?будет (?:еще )?в наличии', message):
        body = ('Дату поступления пока не можем подтвердить. '
                'Когда товар появится в наличии, это будет видно в его карточке.')
    elif re.fullmatch(r'когда (?:доставят|доставят товар|будет доставка|придет заказ)', message):
        body = 'Когда доставят товар, можно узнать при оформлении заказа. Там отображается актуальная дата доставки для Вашего адреса.'
    elif re.fullmatch(r'сколько (?:штук|единиц) в (?:комплекте|упаковке)', message):
        allowed = GENERAL_RULES
        container = message.rsplit(' ', 1)[-1]
        # Use only explicit current card fields for the exact unit requested.
        # Contents and retail-pack counts can be different.
        field = re.compile(rf'количество(?:\s+товаров)?\s+в\s+{container}\s*:\s*([1-9][0-9]{{0,3}})\s*(?:шт\.?|ед\.?)?', re.I)
        values = {match[1] for fact in facts
                  if fact.get('verification_status') == 'VERIFIED'
                  and not fact.get('shared_from_product')
                  and fact.get('source') in ('Карточка товара', 'Карточка WB: характеристики')
                  for line in fact.get('text', '').splitlines()
                  if (match := field.fullmatch(line.strip()))}
        if len(values) != 1:
            return None
        body = f'По данным карточки товара, в {container} указано {values.pop()} шт.'
    else:
        return None
    applicable, _ = select_rules(rules, question, product)
    if any(digest(rule) not in allowed and constraints([rule])['semantic'] for rule in applicable):
        return None
    answer, _ = apply_local_preferences('Добрый день! ' + body, question, rules)
    try:
        answer = validate_reply(apply_signature(answer, rules))
    except ValueError:
        return None
    return answer if not local_issues(answer, rules, question) else None


def stale_question_template(db, review, draft, product, rules):
    proof = (draft.quality or {}).get('owner_rules') or {}
    if proof.get('verification_method') != METHOD:
        return False
    from .services import trusted_product_facts
    facts = trusted_product_facts(db, product)
    return question_template(review, product, rules, facts) != draft.text
