"""Reviewed whole-message equivalence, never keyword or fuzzy-text similarity."""

VERSION = 'equivalent-review-2026-11-12-v1'
GROUPS = {
    'no_scent': {'не пахнет', 'запаха нет', 'нет запаха', 'аромата нет', 'нет аромата',
                 'аромат не чувствуется', 'запах не чувствуется'},
    'weak_scent': {'слабый запах', 'слабый аромат', 'слабо пахнет', 'пахнет слабо'},
    'no_result': {'не помогло', 'не помог', 'нет результата', 'результата нет'},
}


def message_group(text):
    value = (text or '').casefold().replace('ё', 'е')
    # A question, qualification, number, vehicle identifier, or another clause
    # must never inherit an approval merely because it contains a familiar phrase.
    if '?' in value:return None
    value = ' '.join(value.split()).strip(' .!…')
    for group, messages in GROUPS.items():
        if value in messages:return group
    from .rule_execution import UNTRIED_TEXTS
    if value in UNTRIED_TEXTS:return 'untried'
    return None
