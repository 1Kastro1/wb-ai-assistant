import json
import re
from uuid import uuid4
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, delete
from .db import Conversation, Message, Memory, Product, Review, Draft, Action, ToolCall, Vehicle, VinSession, setting, public
from .services import reviews_search, analytics, generate_draft, propose_publish, edit_draft
from .integrations import ollama
from .safety import mask_vin, VIN


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    q: str = Field(default='', max_length=200)
    unanswered: bool = False
    max_rating: int = Field(default=5, ge=1, le=5)
    days: int = Field(default=0, ge=0, le=3650)
    limit: int = Field(default=20, ge=1, le=100)
    product_id: str = ''


class ToolRegistry:
    risk = {
        'reviews.search': 'READ', 'reviews.get': 'READ', 'reviews.get_recent': 'READ', 'reviews.get_unanswered': 'READ',
        'reviews.generate_reply': 'DRAFT', 'reviews.create_draft': 'DRAFT', 'reviews.update_draft': 'DRAFT', 'reviews.publish': 'EXECUTE',
        'products.search': 'READ', 'products.get': 'READ', 'analytics.review_summary': 'READ', 'analytics.product_summary': 'READ',
        'memory.search': 'READ', 'memory.propose': 'DRAFT', 'memory.save': 'EXECUTE',
        'system.status': 'READ', 'system.security_status': 'READ',
        'compatibility.decode_vin': 'READ', 'compatibility.check': 'READ', 'compatibility.find_products': 'READ',
    }

    def authorize(self, name):
        if name not in self.risk:
            raise ValueError('Этот инструмент не разрешён')
        if self.risk[name] == 'EXECUTE':
            raise ValueError('EXECUTE требует Pending Action и подтверждения владельца')

    async def dispatch(self, db, name, arguments, context):
        """LLM can propose an EXECUTE action but cannot confirm it."""
        if name not in self.risk:
            raise ValueError('Неизвестный инструмент')
        if name in ('reviews.publish', 'memory.save'):
            if name == 'reviews.publish':
                return propose_publish(db, context.get('drafted_ids') or context.get('last_result_ids', []))
            name = 'memory.propose'
        else:
            self.authorize(name)
        if name in ('reviews.search', 'reviews.get_recent', 'reviews.get_unanswered'):
            args = ToolArgs(**arguments).model_dump()
            if name == 'reviews.get_recent': args['days'] = args['days'] or 7
            if name == 'reviews.get_unanswered': args['unanswered'] = True
            args['account_id'] = context.get('active_store_id', '')
            rows = reviews_search(db, **args)
            context.update(last_result_ids=[r['id'] for r in rows], active_filters=args, active_task='reviews')
            return rows
        if name == 'reviews.get':
            rid = arguments.get('review_id') or context.get('selected_review_id')
            if rid not in context.get('last_result_ids', []): raise ValueError('Выберите отзыв из текущей выборки')
            row = db.get(Review, rid)
            return public(row) if row else None
        if name in ('reviews.generate_reply', 'reviews.create_draft', 'reviews.update_draft'):
            ids = context.get('last_result_ids', [])
            index = int(arguments.get('index', 1)) - 1
            if index < 0 or index >= len(ids): raise ValueError('Нет такого отзыва в текущей выборке')
            rid = ids[index]
            # For update, only ask the local generator to revise; it cannot publish.
            result = await generate_draft(db, rid, str(arguments.get('instruction', ''))[:1000])
            context['drafted_ids'] = list(dict.fromkeys(context.get('drafted_ids', []) + [rid]))
            return result
        if name in ('products.search', 'products.get'):
            if name == 'products.get':
                product = db.get(Product, str(arguments.get('product_id') or context.get('selected_product_id','')))
                if not product:
                    raise ValueError('Товар не найден')
                context['selected_product_id'] = product.id
                return public(product)
            query = str(arguments.get('q', ''))[:200]
            rows = db.scalars(select(Product).where(Product.name.contains(query, autoescape=True) | Product.brand.contains(query, autoescape=True)).limit(50)).all()
            return [public(r) for r in rows]
        if name.startswith('analytics.'):
            return analytics(db, max(0, min(3650, int(arguments.get('days', 0)))), str(arguments.get('product_id', '')), context.get('active_store_id', ''))
        if name == 'memory.search':
            query = str(arguments.get('q', ''))[:200]
            return [public(m) for m in db.scalars(select(Memory).where(Memory.enabled == True, Memory.text.contains(query, autoescape=True)).limit(20))]
        if name == 'memory.propose':
            value = str(arguments.get('text', '')).strip()
            if not 2 <= len(value) <= 2000: raise ValueError('Укажите текст правила')
            action = Action(id=str(uuid4()), kind='memory', payload={'text':mask_vin(value),'scope':'global','kind':'GLOBAL_RULE'})
            db.add(action); db.commit()
            return public(action)
        if name == 'compatibility.decode_vin':
            from .compatibility import validate_vin
            from .security import digest
            vin = validate_vin(str(arguments.get('vin', '')))
            salt = setting(db, 'vin_salt')
            known = db.get(VinSession, digest(salt + vin)) if salt else None
            if known:
                context['selected_vehicle_id'] = known.vehicle_id
                return {'status':'LOCAL_MATCH','profile':db.get(Vehicle,known.vehicle_id).profile,'vin_mask':known.vin_mask}
            return {'status':'INSUFFICIENT_DATA','vin_mask':mask_vin(vin),'reason':'Нужен подтверждённый профиль автомобиля'}
        if name in ('compatibility.check', 'compatibility.find_products'):
            from .compatibility import check_compatibility
            vehicle = db.get(Vehicle, context.get('selected_vehicle_id', ''))
            if not vehicle: return {'status':'INSUFFICIENT_DATA','reason':'Сначала выберите подтверждённый профиль автомобиля'}
            return check_compatibility(db, vehicle.profile, part_number=str(arguments.get('part_number','')), oe=str(arguments.get('oe','')), same_brand=str(arguments.get('same_brand','')), category=str(arguments.get('category','')))
        return {'privacy_mode':'LOCAL_FIRST','safe_mode':setting(db,'safe_mode',False),'test_mode':setting(db,'test_mode',True),'cloud_ai':False,'web_research':True}


registry = ToolRegistry()


def speech_version(answer: str) -> str:
    """Turn a screen-oriented answer into a concise phrase suitable for TTS."""
    value = re.sub(r'https?://\S+', 'ссылка доступна на экране', answer)
    value = re.sub(r'[`*_#>|\[\]{}]', ' ', value)
    value = re.sub(r'\b(?:VERIFIED_FIT|INSUFFICIENT_DATA|LOCAL_FIRST)\b', '', value)
    value = re.sub(r'\s+', ' ', value).strip()
    sentences = re.split(r'(?<=[.!?])\s+', value)
    spoken = ' '.join(sentences[:4]).strip()
    if len(spoken) > 850:
        spoken = spoken[:850].rsplit(' ', 1)[0] + '.'
    return spoken or 'Результат показан на экране.'


class AssistantService:
    MAX_TOOL_CALLS_PER_MESSAGE = 10

    async def send(self, db, text, conversation_id=None, selection=None, voice_mode=False, can_manage_wb=True):
        def require_wb_operation(tool_name):
            if not can_manage_wb and tool_name in ('reviews.generate_reply', 'reviews.create_draft', 'reviews.update_draft', 'reviews.publish'):
                raise ValueError('Операции Wildberries доступны владельцу и менеджеру WB')
        conversation = db.get(Conversation, conversation_id) if conversation_id else None
        if not conversation:
            conversation = Conversation(id=str(uuid4()), title=mask_vin(text[:70]), context={})
            db.add(conversation)
            db.commit()
        context = dict(conversation.context)
        if (selection or {}).get('active_store_id'):
            context['active_store_id'] = str(selection['active_store_id'])
        for key, cls in (('selected_product_id',Product),('selected_review_id',Review),('selected_vehicle_id',Vehicle)):
            value = (selection or {}).get(key)
            if value and db.get(cls,value):
                context[key] = value
        db.add(Message(id=str(uuid4()), conversation_id=conversation.id, role='user', text=mask_vin(text)))
        db.commit()
        lower = text.lower()
        calls = 0
        data = None
        tool = 'system.status'
        try:
            if 'запомни' in lower:
                rule = text.split(':', 1)[-1].strip()
                action = Action(id=str(uuid4()), kind='memory', payload={'text': mask_vin(rule), 'kind': 'GLOBAL_RULE', 'scope': 'global'})
                db.add(action)
                db.commit()
                data = public(action)
                tool = 'memory.propose'
                answer = 'Предложено правило. Выберите область и подтвердите сохранение в разделе «Действия».'
            elif any(x in lower for x in ('публикуй', 'опубликуй', 'отправь ответы')):
                require_wb_operation('reviews.publish')
                ids = context.get('drafted_ids') or context.get('last_result_ids', [])
                data = propose_publish(db, ids)
                context['pending_action_id'] = data['id']
                tool = 'reviews.publish'
                answer = f"Подготовлено подтверждение: {len(data['payload']['items'])} ответов. Откройте «Действия», проверьте тексты и подтвердите."
            elif any(x in lower for x in ('подготовь ответ', 'ответь на', 'создай ответ', 'сделай короче')):
                require_wb_operation('reviews.generate_reply')
                if 'отзыв' in lower and any(x in lower for x in ('найди', 'негатив', 'без ответа')):
                    args = ToolArgs(unanswered='без ответа' in lower, max_rating=3 if 'негатив' in lower else 5, days=7 if 'недел' in lower else 0, limit=9)
                    data = await registry.dispatch(db, 'reviews.search', args.model_dump(), context)
                    calls += 1
                ids = context.get('last_result_ids', [])
                if 'перв' in lower:
                    n = 2 if 'два' in lower or 'двух' in lower else 1
                    ids = ids[:n]
                elif 'остальн' in lower:
                    ids = [i for i in ids if i not in context.get('drafted_ids', [])]
                if not ids:
                    raise ValueError('Сначала найдите отзывы, на которые нужно ответить')
                if len(ids) + calls > self.MAX_TOOL_CALLS_PER_MESSAGE:
                    raise ValueError('За одно сообщение можно подготовить до 10 ответов. Уточните выборку')
                data = []
                for rid in ids:
                    data.append(await generate_draft(db, rid, text))
                    calls += 1
                context['drafted_ids'] = ids
                tool = 'reviews.generate_reply'
                answer = f'Создано локальных черновиков: {len(data)}. Проверьте их в отзывах. В Wildberries ничего не опубликовано.'
            elif 'вниман' in lower or 'сегодня' == lower.strip(' ?.') or 'проанализ' in lower or 'аналитик' in lower:
                tool = 'analytics.review_summary'
                data = analytics(db, days=30 if 'месяц' in lower else 0, product_id=context.get('selected_product_id', '') if 'товар' in lower else '')
                answer = f"В выбранных локальных данных {data['total']} отзывов; без ответа — {data['unanswered']}, негативных — {data['negative']}, требуют ручной проверки — {data['manual']}. Средняя оценка: {data['average']}."
            elif 'совместим' in lower or 'подойдут' in lower or 'vin' in lower or 'вин' in lower or VIN.search(text):
                tool = 'compatibility.check'
                vin = VIN.search(text)
                if vin:
                    data = await registry.dispatch(db,'compatibility.decode_vin',{'vin':vin[0]},context)
                    calls += 1
                product = db.get(Product,context.get('selected_product_id',''))
                if context.get('selected_vehicle_id'):
                    args = {'same_brand':product.brand if product else ''}
                    if product and product.part_number and 'подойдут' in lower:
                        args['part_number'] = product.part_number
                    if 'колодк' in lower:
                        args['category'] = 'brake_pads'
                    data = await registry.dispatch(db,'compatibility.find_products',args,context)
                    calls += 1
                    fitting = [r for r in data['items'] if r['recommendable']]
                    if fitting:
                        answer = 'По подтверждённому профилю и каталогу найдены совместимые товары:\n' + '\n'.join(f"{r['brand']} · {r['part_number']} · WB {r['product_id']} · VERIFIED_FIT" for r in fitting) + '\nИсточники и условия доступны в результате проверки. Без подтверждения ответа публикация не выполняется.'
                    else:
                        answer = 'Товаров, допустимых для рекомендации, не найдено.\n'+'\n'.join(f"{r['brand']} {r['part_number']}: {r['status']}; {'; '.join(r['conditions'])}" for r in data['items'])
                else:
                    answer = 'INSUFFICIENT_DATA. Для проверки откройте «Подбор запчастей»: укажите VIN или подтверждённый профиль автомобиля и артикул. Без точного профиля и подтверждённого каталога совместимость не подтверждаю.'
            elif 'товар' in lower and not 'отзыв' in lower:
                tool = 'products.search'
                data = [public(p) for p in db.scalars(select(Product).limit(50))]
                answer = f'В локальной базе товаров: {len(data)} (показано до 50).'
            elif any(x in lower for x in ('отзыв', 'негатив', 'без ответа', 'скрип', 'пахнет', 'последнюю неделю', 'только')):
                tool = 'reviews.search'
                args = dict(context.get('active_filters', {})) if 'только' in lower else {}
                args.update({'unanswered': 'без ответа' in lower or args.get('unanswered', False), 'max_rating': 3 if ('негатив' in lower or '1–3' in lower or '1-3' in lower) else args.get('max_rating', 5), 'days': 7 if 'недел' in lower else 1 if 'сегодня' in lower else 30 if 'месяц' in lower else args.get('days', 0), 'limit': 5 if 'пять' in lower else 20})
                for keyword in ('скрип', 'EIKOSHA', 'упаков'):
                    if keyword.lower() in lower:
                        args['q'] = keyword
                numbers = re.search(r'покажи\s+(\d+)', lower)
                if numbers:
                    args['limit'] = min(100, int(numbers[1]))
                args = ToolArgs(**args).model_dump()
                data = reviews_search(db, **args)
                context['last_result_ids'], context['active_filters'] = [r['id'] for r in data], args
                context['active_task'] = 'reviews'
                answer = f'Найдено отзывов: {len(data)}. ' + ('Можно подготовить ответы на выбранные отзывы.' if data else 'Измените фильтр или синхронизируйте данные WB в настройках.')
            else:
                history = db.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at.desc()).limit(8)).all()
                schema = {'type':'object','properties':{'tool':{'type':'string','enum':['none',*registry.risk]},'arguments':{'type':'object'},'answer':{'type':'string'}},'required':['tool','arguments','answer']}
                voice_instruction = ' Пользователь говорит голосом: отвечай естественно, короткими фразами, без таблиц и служебной разметки.' if voice_mode else ''
                plan = await ollama.structured([{'role':'system','content':'Ты локальный помощник WB. Верни JSON: tool, arguments, answer. Выбери не более одного инструмента. Для обычной беседы tool=none. Не выдумывай числа, факты или выполненные действия. reviews.search принимает q, unanswered, max_rating, days, limit; генерация — index (от 1), instruction; memory.propose — text. Публикация лишь предлагает подтверждение. Никогда не утверждай совместимость без результата каталога.'+voice_instruction+' Контекст: '+json.dumps(context,ensure_ascii=False)}, *[{'role':m.role,'content':m.text[:3000]} for m in reversed(history)]], schema)
                tool = plan.get('tool', 'none')
                if tool == 'none':
                    tool = 'system.status'
                    answer = str(plan.get('answer','Уточните запрос.'))
                else:
                    require_wb_operation(tool)
                    data = await registry.dispatch(db, tool, plan.get('arguments') or {}, context)
                    calls += 1
                    if tool.startswith('reviews.') and isinstance(data, list):
                        answer = f'Найдено отзывов: {len(data)}. Результаты доступны в разделе «Отзывы».'
                    elif tool in ('reviews.publish','memory.propose','memory.save'):
                        answer = 'Предложение подготовлено. Проверьте его и подтвердите в разделе «Действия».'
                    else:
                        # Do not let a second LLM transform structured evidence into false fitment.
                        answer = 'Результат инструмента '+tool+':\n'+json.dumps(data,ensure_ascii=False,indent=2)[:10000]
            if tool not in ('reviews.publish', 'memory.save'):
                registry.authorize(tool)
            if calls > self.MAX_TOOL_CALLS_PER_MESSAGE:
                raise ValueError('Достигнут лимит инструментов')
            db.add(ToolCall(name=tool, status='PROPOSED' if tool in ('reviews.publish', 'memory.save') else 'OK'))
        except ValueError as error:
            db.rollback()
            answer = str(error)
            data = None
            db.add(ToolCall(name=tool, status='ERROR'))
        answer = mask_vin(answer)
        conversation.context = context
        # Summary contains state, not unbounded raw transcript.
        conversation.summary = json.dumps({'task': context.get('active_task'), 'filters': context.get('active_filters'), 'last_response': answer[:700]}, ensure_ascii=False)
        db.add(Message(id=str(uuid4()), conversation_id=conversation.id, role='assistant', text=answer))
        db.commit()
        return {'conversation_id': conversation.id, 'text': answer, 'speech_text': speech_version(answer) if voice_mode else answer, 'data': data, 'context': context}


assistant = AssistantService()
