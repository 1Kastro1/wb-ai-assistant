"""Question rule previews: no saved drafts, catalog requests or publication actions."""
import asyncio
import json
from pathlib import Path
from .db import Review
from .fitment_catalog import fitment_answer, vehicle_identifiers, oem_numbers
from .integrations import ollama
from .owner_reply_rules import apply_owner_preferences
from .reply_policy import analyze_customer_message, official_support_reply
from .safety import validate_reply, mask_vin


async def preview_question(db, product, example, rules, facts, store):
    from .services import question_intent, safe_question_fallback, assess_question_answer
    question = Review(id='rule-preview', product_id=product.id, wb_account_id=store,
                      marketplace='wb_question', text=example, rating=0)
    intent = question_intent(example)
    catalog = None
    if vehicle_identifiers(example) or oem_numbers(example):
        # This only reads previously confirmed evidence. It never opens Uniqom or
        # calls a remote catalog, and any salt initialization stays in a savepoint.
        transaction = db.begin_nested()
        try:
            catalog = fitment_answer(db, question, product)
        finally:
            transaction.rollback()
    fallback = catalog['text'] if catalog else safe_question_fallback(question, product, intent)
    reply = fallback
    note = 'Проверка примера не открывает Uniqom и не выполняет новый подбор. Использованы только сохранённые подтверждения.' if catalog else 'Предварительный ответ на вопрос. Правило и ответ не сохраняются.'
    if analyze_customer_message(example)['buyer_asks_about_money']:
        reply = official_support_reply()
    elif not catalog:
        messages = [
            {'role': 'system', 'content': (Path(__file__).parent / 'prompts/question_reply.md').read_text(encoding='utf-8')},
            {'role': 'user', 'content': json.dumps({'question': example, 'question_type': intent,
                'product': product.name, 'brand': product.brand, 'verified_facts': facts,
                'store_rules': rules}, ensure_ascii=False)},
        ]
        try:
            reply = await asyncio.wait_for(ollama.chat(messages), timeout=12)
            reply = validate_reply(mask_vin(reply))
            if not assess_question_answer(question, product, reply, facts)['passed']:
                reply = fallback
        except Exception:
            reply = fallback
            note = 'Показан безопасный пример ответа на вопрос. Модель сейчас недоступна; подбор в Uniqom не выполнялся.'
    reply, _ = apply_owner_preferences(reply, question, rules, style_only=bool(catalog))
    try:
        reply = validate_reply(mask_vin(reply))
    except ValueError:
        reply, _ = apply_owner_preferences(fallback, question, rules, style_only=bool(catalog))
        reply = validate_reply(mask_vin(reply))
    quality = assess_question_answer(question, product, reply, catalog['sources'] if catalog else facts)
    if catalog:
        quality['fitment'] = catalog
        quality['passed'] = catalog['status'] in ('FIT', 'ALTERNATIVE', 'UNAVAILABLE', 'NEEDS_VIN')
        if not quality['passed']:
            quality['issues'] = ['Нужно проверить оригинальный OEM и аналог в каталоге Uniqom.']
    return {'text': reply, 'quality': quality, 'note': note, 'kind': 'QUESTION_RULE'}
