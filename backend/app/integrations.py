import asyncio
import html
import json
import re
from datetime import datetime, timezone
from xml.etree import ElementTree
from urllib.parse import parse_qs, unquote, urlsplit
from sqlalchemy import select, func
from .config import MODEL
from .db import StoreListing, Account, Draft, Product, Review, StoreProfile, Session, now, setting, set_setting
from .safety import classify, mask_vin
from .verified_instructions import attach_verified_instructions
from .product_images import card_image_url
from .security import guard, decrypt_secret


class OllamaProvider:
    async def health_check(self):
        return await guard.request('ollama', 'health')

    async def list_models(self):
        return (await self.health_check()).get('models', [])

    async def chat(self, messages, schema=None, *, task=None):
        from .model_router import ModelRouter
        messages = [dict(message) for message in messages]
        if messages and messages[-1].get('role') == 'user':
            # Qwen 3 understands this template command even when an older Ollama build
            # ignores the API-level `think: false` flag.
            messages[-1]['content'] = messages[-1].get('content', '') + '\n/no_think'
        body = {**ModelRouter().choose(task or ('FAST' if schema else 'STANDARD')), 'messages': messages, 'stream': False, 'think': False}
        # Cyrillic policy lists need substantially more tokens than English text.
        # Never silently truncate the beginning (system instructions / conditions).
        estimated = sum(len(message.get('content', '').encode('utf-8')) for message in messages) // 3
        needed = estimated + body['options']['num_predict'] + 512
        if needed > 16384:
            raise ValueError('Правила слишком длинные для контекста модели; сократите повторы или область применения')
        body['options']['num_ctx'] = max(body['options']['num_ctx'], ((needed + 1023) // 1024) * 1024)
        if schema:
            body['format'] = schema
        from .generation_budget import OWNER_RULE_CHECK_SECONDS
        request_options = {'timeout_seconds': OWNER_RULE_CHECK_SECONDS} if task == 'OWNER_RULES' else {}
        result = await guard.request('ollama', 'chat', body=body, **request_options)
        if result.get('prompt_eval_count', 0) >= body['options']['num_ctx'] - 8:
            raise ValueError('Контекст модели переполнен; сократите повторяющиеся правила или их область применения')
        content = result['message']['content']
        # Some installed Qwen templates include reasoning in content despite think=false.
        if '</think>' in content:
            content = content.rsplit('</think>', 1)[-1]
        elif '<think>' in content:
            raise ValueError('Локальная модель не завершила ответ. Повторите запрос.')
        return content.strip()

    async def structured(self, messages, schema, *, task=None):
        return json.loads(await self.chat(messages, schema, task=task) if task else await self.chat(messages, schema))

    async def reply_variants(self, messages, count=3):
        """Request several customer-facing variants in one model call."""
        request = [dict(message) for message in messages] + [{
            'role': 'user',
            'content': (
                f'Создай {count} разных безопасных вариантов ответа. Верни только JSON без Markdown: '
                '{"variants":["ответ 1","ответ 2","ответ 3"]}. '
                'Внутри variants должны быть только готовые ответы покупателю, без анализа, правил, пояснений и служебного текста.'
            ),
        }]
        if count == 1:
            request[-1]['content'] = ('Подготовь один краткий ответ покупателю до 450 символов. '
                'Верни только JSON {"variants":["готовый ответ"]}, без рассуждений. '
                'Отвечай по существу только по подтверждённым сведениям.')
            schema = {'type': 'object', 'properties': {'variants': {'type': 'array', 'items': {'type': 'string', 'maxLength': 450},
                       'minItems': 1, 'maxItems': 1}}, 'required': ['variants'], 'additionalProperties': False}
            raw = await self.chat(request, schema=schema)
        else:
            raw = await self.chat(request)
        try:
            match = re.search(r'\{.*\}', raw, re.S)
            payload = json.loads(match.group(0) if match else raw)
            variants = [str(value).strip() for value in payload.get('variants', []) if str(value).strip()]
            return variants[:count] or [raw]
        except (ValueError, TypeError, json.JSONDecodeError):
            return [raw]


ollama = OllamaProvider()


class WebResearchProvider:
    @staticmethod
    def clean(value):
        return html.unescape(re.sub(r'<[^>]+>', ' ', value)).replace('\xa0', ' ').strip()

    async def search_instructions(self, product, db=None, topic='инструкция применения'):
        article = str(getattr(product, 'part_number', '') or '').strip()
        identity = article if article else product.name
        query = f'"{product.brand}" "{identity}" {topic}'
        owned = db is None
        if owned:
            db = Session()
        try:
            provider = setting(db, 'search_provider', 'duckduckgo')
            key_row = db.get(Account, 'search:tavily')
            if provider == 'tavily' and key_row:
                response = await guard.request('tavily', 'search', token='Bearer ' + decrypt_secret(key_row.encrypted_token), body={
                    'query': query, 'search_depth': 'advanced', 'max_results': 5, 'include_answer': False,
                })
                return self._normalized_results(product, [
                    {'title': item.get('title', ''), 'snippet': item.get('content', ''), 'url': item.get('url', '')}
                    for item in response.get('results', [])
                ])
        finally:
            if owned:
                db.close()
        try:
            page = await guard.request('web', 'search', params={'q': query, 'kl': 'ru-ru'}, return_text=True)
            lowered = page.lower()
            if 'please complete the following challenge' in lowered or 'bots use duckduckgo too' in lowered:
                raise ValueError('Поисковый сервис запросил проверку браузера')
            links = re.findall(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.I | re.S)
            snippets = re.findall(r'<(?:a|div)[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>', page, re.I | re.S)
        except ValueError:
            links, snippets = [], []
        wanted = {x.lower() for x in re.findall(r'[а-яёa-z0-9]{4,}', product.name + ' ' + product.brand, re.I)}
        raw = []
        for index, (href, title) in enumerate(links[:10]):
            parsed = urlsplit(html.unescape(href))
            if parsed.netloc.endswith('duckduckgo.com'):
                href = unquote(parse_qs(parsed.query).get('uddg', [''])[0])
            snippet = self.clean(snippets[index]) if index < len(snippets) else ''
            title = self.clean(title)
            raw.append({'title': title, 'snippet': snippet, 'url': href})
        results = self._normalized_results(product, raw)
        if results:
            return results
        try:
            brave = await guard.request('web', 'brave_search', params={'q': query}, return_text=True)
            blocks = re.split(r'<div class="snippet [^"]*" data-pos="\d+" data-type="web"', brave, flags=re.I)
            brave_items = []
            for block in blocks[1:11]:
                link = re.search(r'<a href="(https?://[^"<>]+)"', block, re.I)
                title = re.search(r'<div class="[^"]*search-snippet-title[^"]*"[^>]*title="([^"]+)"', block, re.I)
                snippet = re.search(r'<div class="content [^"]*"[^>]*>(.*?)</div>', block, re.I | re.S)
                if link and title:
                    brave_items.append({'url': html.unescape(link.group(1)), 'title': html.unescape(title.group(1)), 'snippet': self.clean(snippet.group(1)) if snippet else ''})
            results = self._normalized_results(product, brave_items)
            if results:
                return results
        except ValueError:
            pass
        rss = await guard.request('web', 'bing_rss', params={'format': 'rss', 'q': query}, return_text=True)
        try:
            root = ElementTree.fromstring(rss)
        except ElementTree.ParseError:
            raise ValueError('Поиск товаров сейчас недоступен; повторите позже') from None
        entries = [{'title': node.findtext('title', ''), 'snippet': node.findtext('description', ''), 'url': node.findtext('link', '')}
                   for node in root.findall('./channel/item')]
        return self._normalized_results(product, entries)

    def _normalized_results(self, product, items):
        wanted = {x.lower() for x in re.findall(r'[а-яёa-z0-9]{4,}', product.name + ' ' + product.brand, re.I)}
        article = re.sub(r'[^a-z0-9]', '', str(getattr(product, 'part_number', '') or '').lower())
        article_pattern = r'(?<![a-z0-9])' + r'[\s._/-]*'.join(re.escape(c) for c in article) + r'(?![a-z0-9])' if len(article) >= 3 else ''
        brand = re.sub(r'[^а-яёa-z0-9]', '', str(product.brand or '').lower())
        results = []
        for item in items:
            source = urlsplit(item.get('url', ''))
            if source.scheme not in ('http', 'https') or not source.netloc:
                continue
            title, snippet = self.clean(item.get('title', '')), self.clean(item.get('snippet', ''))
            found = {x.lower() for x in re.findall(r'[а-яёa-z0-9]{4,}', title + ' ' + snippet, re.I)}
            score = len(wanted & found)
            evidence = title + ' ' + snippet + ' ' + unquote(item['url'])
            exact = bool(article_pattern and brand and re.search(article_pattern, evidence, re.I)
                         and brand in re.sub(r'[^а-яёa-z0-9]', '', evidence.lower()))
            if exact or (not article_pattern and score >= min(3, max(1, len(wanted)))):
                results.append({'title': title[:300], 'snippet': snippet[:1200], 'url': item['url'], 'host': source.netloc.lower(), 'match_score': score})
        return results[:5]


web_research = WebResearchProvider()


def wb_token(db, account_id=None):
    account_id = account_id or setting(db, 'active_store', 'owner')
    row = db.get(Account, account_id)
    if not row:
        raise ValueError('Сначала сохраните WB API Token в настройках')
    return decrypt_secret(row.encrypted_token)


async def wb_question_pages(token, answered, cancelled=None, page_size=5000, window_size=10000, start=None, with_checkpoint=False):
    """Read all questions without exceeding WB's take + skip <= 10000 limit."""
    date_to = (start or {}).get('date_to')
    start_skip = int((start or {}).get('skip', 0))
    seen = set()
    while True:
        last_item = None
        for skip in range(start_skip, window_size, page_size):
            if cancelled and cancelled():
                raise asyncio.CancelledError()
            params = {'isAnswered': str(answered).lower(), 'take': page_size,
                      'skip': skip, 'order': 'dateDesc'}
            if date_to is not None:
                params['dateTo'] = date_to
            result = await guard.request('wb', 'questions', token=token, params=params)
            if result.get('error'):
                raise ValueError('WB сообщил об ошибке получения вопросов')
            items = result.get('data', {}).get('questions', [])
            fresh = [item for item in items if item.get('id') not in seen]
            seen.update(item.get('id') for item in fresh)
            next_cursor = {'date_to': date_to, 'skip': skip + page_size}
            if len(items) >= page_size and next_cursor['skip'] >= window_size and items[-1].get('createdDate'):
                timestamp = datetime.fromisoformat(str(items[-1]['createdDate']).replace('Z', '+00:00'))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                next_cursor = {'date_to': int(timestamp.timestamp()), 'skip': 0}
            if fresh:
                yield (fresh, next_cursor) if with_checkpoint else fresh
            if len(items) < page_size:
                return
            last_item = items[-1]
        if not last_item or not last_item.get('createdDate'):
            raise ValueError('WB не указал дату для продолжения списка вопросов')
        timestamp = datetime.fromisoformat(str(last_item['createdDate']).replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        next_date_to = int(timestamp.timestamp())
        if date_to is not None and next_date_to >= date_to:
            raise ValueError('WB вернул повторный временной курсор списка вопросов')
        date_to = next_date_to
        start_skip = 0


class WildberriesProvider:
    async def sync_products(self, db, account_id=None, progress=None, cancelled=None, resume=None, checkpoint=None, between_pages=None):
        token = wb_token(db, account_id)
        cursor, total = (resume or {}).get('cursor') or {'limit': 100}, int((resume or {}).get('processed', 0))
        seen = set()
        for _ in range(1000):
            if cancelled and cancelled():
                raise asyncio.CancelledError()
            result = await guard.request('wb', 'products', token=token, body={'settings': {'sort': {'ascending': True}, 'filter': {'withPhoto': -1}, 'cursor': cursor}})
            for item in result.get('cards', []):
                pid = str(item['nmID'])
                db.merge(StoreListing(store_id=account_id or 'owner',product_id=pid,seen_at=now()))
                product = db.get(Product, pid) or Product(id=pid)
                product.name, product.brand, product.category = item.get('title', ''), item.get('brand', ''), item.get('subjectName', '')
                product.image_url = card_image_url(item)
                facts = [f for f in (product.facts or []) if not str(f.get('source', '')).startswith('Карточка WB')]
                description = str(item.get('description') or '').strip()
                if description:
                    facts.append({'text': description[:8000], 'source': 'Карточка WB: описание', 'verified_at': item.get('updatedAt') or now(), 'verification_status': 'VERIFIED'})
                characteristics = []
                for characteristic in item.get('characteristics') or []:
                    name, value = str(characteristic.get('name') or '').strip(), characteristic.get('value')
                    if isinstance(value, list):
                        value = ', '.join(str(x) for x in value)
                    if name and value not in (None, '', []):
                        characteristics.append(f'{name}: {value}')
                if characteristics:
                    facts.append({'text': '\n'.join(characteristics)[:8000], 'source': 'Карточка WB: характеристики', 'verified_at': item.get('updatedAt') or now(), 'verification_status': 'VERIFIED'})
                product.facts = facts
                attach_verified_instructions(db, product)
                # vendorCode is seller SKU, not verified manufacturer part number.
                db.add(product)
                total += 1
                if total % 100 == 0 and between_pages:
                    db.commit()
                    await between_pages()
            db.commit()
            if progress:
                progress(min(95, total // 150 + 5))
            next_cursor = result.get('cursor', {})
            if next_cursor.get('total', 0) < 100:
                return total
            key = (next_cursor.get('updatedAt'), next_cursor.get('nmID'))
            if key in seen:
                raise ValueError('WB вернул повторный курсор; синхронизация остановлена')
            seen.add(key)
            cursor = {'limit': 100, 'updatedAt': key[0], 'nmID': key[1]}
            if checkpoint: checkpoint({'cursor': cursor, 'processed': total})
            await asyncio.sleep(0.65)
        raise ValueError('Достигнут лимит страниц синхронизации')

    async def sync_reviews(self, db, account_id=None, progress=None, cancelled=None, resume=None, checkpoint=None, between_pages=None):
        account_id = account_id or setting(db, 'active_store', 'owner')
        token, total = wb_token(db, account_id), int((resume or {}).get('processed', 0))
        for answered in (False, True):
            if (resume or {}).get('answered') is True and not answered:
                continue
            start_skip = int((resume or {}).get('skip', 0)) if (resume or {}).get('answered') == answered else 0
            for skip in range(start_skip, 200000, 5000):
                if cancelled and cancelled():
                    raise asyncio.CancelledError()
                result = await guard.request('wb', 'reviews', token=token, params={'isAnswered': str(answered).lower(), 'take': 5000, 'skip': skip, 'order': 'dateDesc'})
                if result.get('error'):
                    raise ValueError('WB сообщил об ошибке получения отзывов')
                items = result.get('data', {}).get('feedbacks', [])
                for item in items:
                    details = item.get('productDetails', {})
                    pid = str(details['nmId'])
                    product = db.get(Product, pid)
                    if not product:
                        product = Product(id=pid, name=details.get('productName', ''), brand=details.get('brandName', ''), category='')
                        db.add(product)
                        db.flush()
                    rid = str(item['id'])
                    row = db.scalar(select(Review).where(Review.wb_account_id == account_id, Review.wb_review_id == rid))
                    if not row:
                        row = Review(id=account_id + ':' + rid, wb_account_id=account_id, wb_review_id=rid, product_id=pid, status='unanswered')
                    text = '\n'.join(str(item.get(k) or '') for k in ('text', 'pros', 'cons')).strip()
                    row.text, row.rating = mask_vin(text), item['productValuation']
                    row.created_at = item.get('createdDate', '')
                    row.is_answered = bool(item.get('answer'))
                    row.existing_answer = mask_vin((item.get('answer') or {}).get('text', ''))
                    for key, value in classify(text, product.category).items():
                        setattr(row, key, value)
                    if row.is_answered:
                        row.status = 'published'
                    elif row.manual:
                        row.status = 'manual_review'
                    db.add(row)
                    total += 1
                    if total % 100 == 0 and between_pages:
                        db.commit()
                        await between_pages()
                db.commit()
                if checkpoint: checkpoint({'answered': answered if len(items) >= 5000 else True, 'skip': skip + 5000 if len(items) >= 5000 else 0, 'processed': total})
                if progress:
                    progress(min(95, 5 + total // 2500))
                if len(items) < 5000:
                    break
                await asyncio.sleep(0.4)
        return total

    async def sync_questions(self, db, account_id=None, progress=None, cancelled=None, resume=None, checkpoint=None, between_pages=None):
        account_id = account_id or setting(db, 'active_store', 'owner')
        token, total = wb_token(db, account_id), int((resume or {}).get('processed', 0))
        for answered in (False, True):
            if (resume or {}).get('answered') is True and not answered:
                continue
            start = (resume or {}).get('cursor') if (resume or {}).get('answered') == answered else None
            async for items, next_cursor in wb_question_pages(token, answered, cancelled, start=start, with_checkpoint=True):
                for item in items:
                    details = item.get('productDetails') or {}
                    pid = str(details.get('nmId') or item.get('nmId') or '')
                    if not pid:
                        continue
                    product = db.get(Product, pid)
                    if not product:
                        product = Product(id=pid, name=details.get('productName', ''), brand=details.get('brandName', ''), category='')
                        db.add(product)
                        db.flush()
                    external_id = str(item.get('id') or '')
                    if not external_id:
                        continue
                    stored_id = 'question:' + external_id
                    row = db.scalar(select(Review).where(Review.wb_account_id == account_id, Review.wb_review_id == stored_id))
                    if not row:
                        row = Review(id=account_id + ':' + stored_id, wb_account_id=account_id, wb_review_id=stored_id, product_id=pid, rating=0, text='', status='unanswered', marketplace='wb_question')
                    answer = item.get('answer') or {}
                    # Preserve the buyer's VIN for the authenticated questions view.
                    row.text = str(item.get('text') or '').strip()
                    row.created_at = str(item.get('createdDate') or now())
                    row.is_answered = bool(answer and answer.get('text'))
                    row.existing_answer = mask_vin(str(answer.get('text') or ''))
                    row.status = 'published' if row.is_answered else ('draft_ready' if db.scalar(select(Draft).where(Draft.review_id == row.id)) else 'unanswered')
                    row.marketplace = 'wb_question'
                    db.add(row)
                    total += 1
                    if total % 100 == 0 and between_pages:
                        db.commit()
                        await between_pages()
                db.commit()
                if checkpoint: checkpoint({'answered': answered, 'cursor': next_cursor, 'processed': total})
                if progress:
                    progress(min(95, 5 + total // 2500))
                await asyncio.sleep(0.4)
            if checkpoint: checkpoint({'answered': True, 'cursor': {'date_to': None, 'skip': 0}, 'processed': total})
        return total

    async def publish(self, db, review, text):
        token = wb_token(db, review.wb_account_id)
        if review.marketplace == 'wb_question':
            question_id = review.wb_review_id.removeprefix('question:')
            current = await guard.request('wb', 'question', token=token, params={'id': question_id})
            if current.get('error') or str(current.get('data', {}).get('id')) != question_id:
                raise ValueError('WB не подтвердил существование выбранного вопроса')
            if (current.get('data', {}).get('answer') or {}).get('text'):
                raise ValueError('На вопрос уже есть ответ в WB')
            await guard.request('wb', 'question_publish', token=token, body={'id': question_id, 'text': text, 'state': 'wbRu'})
            return
        current = await guard.request('wb', 'review', token=token, params={'id': review.wb_review_id})
        if current.get('error') or current.get('data', {}).get('id') != review.wb_review_id:
            raise ValueError('WB не подтвердил существование выбранного отзыва')
        if current.get('data', {}).get('answer'):
            raise ValueError('У отзыва уже есть ответ в WB')
        await guard.request('wb', 'publish', token=token, body={'id': review.wb_review_id, 'text': text})


wb = WildberriesProvider()


def store_credentials(db, store_id):
    store = db.get(StoreProfile, store_id)
    account = db.get(Account, store_id)
    if not store or not account:
        raise ValueError('Сначала подключите API выбранного магазина')
    return store, decrypt_secret(account.encrypted_token)


def upsert_market_review(db, store, external_id, product_external_id, product_name, brand, rating, text, created_at, answered=False, answer=''):
    product_id = f'{store.provider}:{store.id}:{product_external_id}'
    if store.provider == 'ozon' and not db.get(Product, product_id):
        product_id = setting(db, f'ozon-product:{store.id}:{product_external_id}', product_id)
    product = db.get(Product, product_id) or Product(id=product_id, name=product_name or str(product_external_id), brand=brand or '', category='', marketplace=store.provider)
    db.add(product)
    db.flush()
    row = db.scalar(select(Review).where(Review.wb_account_id == store.id, Review.wb_review_id == str(external_id)))
    if not row:
        row = Review(id=f'{store.id}:{external_id}', wb_account_id=store.id, wb_review_id=str(external_id), product_id=product_id, status='unanswered', marketplace=store.provider)
    row.text, row.rating, row.created_at = mask_vin(text or ''), int(rating or 0), str(created_at or now())
    row.is_answered, row.existing_answer = bool(answered), mask_vin(answer or '')
    for key, value in classify(row.text, product.category).items():
        setattr(row, key, value)
    row.status = 'published' if row.is_answered else ('manual_review' if row.manual else ('draft_ready' if db.scalar(select(Draft).where(Draft.review_id == row.id)) else 'unanswered'))
    db.add(row)
    return row


def upsert_market_question(db, store, external_id, product_external_id, product_name, text, created_at, answered=False):
    if not external_id:
        return None
    product_id = f'{store.provider}:{store.id}:{product_external_id or "unknown"}'
    if store.provider == 'ozon' and not db.get(Product, product_id):
        product_id = setting(db, f'ozon-product:{store.id}:{product_external_id}', product_id)
    product = db.get(Product, product_id) or Product(id=product_id, name=product_name or str(product_external_id or 'Товар'), brand='', category='', marketplace=store.provider)
    db.add(product)
    db.flush()
    stored_id = f'question:{external_id}'
    row = db.scalar(select(Review).where(Review.wb_account_id == store.id, Review.wb_review_id == stored_id))
    if not row:
        row = Review(id=f'{store.id}:{stored_id}', wb_account_id=store.id, wb_review_id=stored_id, product_id=product_id, rating=0, status='unanswered', marketplace=f'{store.provider}_question')
    # Preserve the buyer's VIN for the authenticated questions view.
    row.text = str(text or '').strip()
    row.created_at = str(created_at or now())
    row.is_answered = bool(answered)
    row.status = 'published' if answered else ('draft_ready' if db.scalar(select(Draft).where(Draft.review_id == row.id)) else 'unanswered')
    db.add(row)
    return row


class OzonProvider:
    async def sync_products(self, db, store_id, progress=None, cancelled=None, resume=None, checkpoint=None, between_pages=None):
        from .ozon_content import sync_catalog
        store, token = store_credentials(db, store_id)
        return await sync_catalog(db, store, self.headers(store, token), progress, cancelled, resume, checkpoint, between_pages)

    def headers(self, store, token):
        client_id = str((store.config or {}).get('client_id', '')).strip()
        if not client_id:
            raise ValueError('Для Ozon укажите Client-Id')
        return {'Client-Id': client_id, 'Api-Key': token}

    async def test(self, db, store_id):
        store, token = store_credentials(db, store_id)
        await guard.request('ozon', 'reviews', headers=self.headers(store, token), body={'limit': 20, 'sort_dir': 'DESC'})
        return True

    async def sync_reviews(self, db, store_id, progress=None, cancelled=None, resume=None, checkpoint=None, between_pages=None):
        store, token = store_credentials(db, store_id)
        total, last_id = int((resume or {}).get('processed', 0)), str((resume or {}).get('last_id', ''))
        seen = {last_id} if last_id else set()
        for _ in range(10000):
            if cancelled and cancelled(): raise asyncio.CancelledError()
            body = {'limit': 100, 'sort_dir': 'DESC'}
            if last_id: body['last_id'] = last_id
            result = await guard.request('ozon', 'reviews', headers=self.headers(store, token), body=body)
            items = result.get('reviews') or result.get('result', {}).get('reviews') or []
            for item in items:
                content = '\n'.join(str(item.get(k) or '') for k in ('text','positive','negative')).strip()
                comments = item.get('comments_amount', 0)
                upsert_market_review(db, store, item.get('id'), item.get('sku') or item.get('product_id') or 'unknown', item.get('product_name') or item.get('sku_name'), '', item.get('rating'), content, item.get('published_at') or item.get('created_at'), bool(comments))
                total += 1
            db.commit()
            if between_pages: await between_pages()
            if progress: progress(min(95, 5 + total // 100))
            payload = result.get('result') or result
            if not items or payload.get('has_next') is False or (len(items) < 100 and not payload.get('has_next')): return total
            last_id = str(payload.get('last_id') or items[-1].get('id') or '')
            if not last_id or last_id in seen:
                raise ValueError('Ozon повторил страницу отзывов; прогресс сохранён')
            seen.add(last_id)
            if checkpoint: checkpoint({'last_id': last_id, 'processed': total})
        raise ValueError('Достигнут лимит страниц отзывов Ozon; продолжите с сохранённого прогресса')

    async def publish(self, db, review, text):
        store, token = store_credentials(db, review.wb_account_id)
        if review.marketplace == 'ozon_question':
            question_id = review.wb_review_id.removeprefix('question:')
            current = await guard.request('ozon', 'question_info', headers=self.headers(store, token), body={'question_id': question_id})
            question = current.get('question') or current.get('result', {}).get('question') or current.get('result') or {}
            if str(question.get('id') or question.get('question_id') or '') != question_id:
                raise ValueError('Ozon не подтвердил существование выбранного вопроса')
            if str(question.get('status') or '').upper() not in ('NEW', 'UNPROCESSED'):
                raise ValueError('Ozon не подтвердил, что вопрос ещё ждёт ответа')
            sku = str(question.get('sku') or '')
            if not sku.isdigit():
                raise ValueError('Ozon не вернул SKU вопроса; публикация остановлена')
            await guard.request('ozon', 'question_publish', headers=self.headers(store, token), body={'question_id': question_id, 'sku': int(sku), 'text': text})
            return
        await guard.request('ozon', 'publish', headers=self.headers(store, token), body={'review_id': review.wb_review_id, 'text': text})

    async def sync_questions(self, db, store_id, progress=None, cancelled=None, resume=None, checkpoint=None, between_pages=None):
        store, token = store_credentials(db, store_id)
        total, last_id, seen = int((resume or {}).get('processed', 0)), str((resume or {}).get('last_id', '')), set()
        for _ in range(1000):
            if cancelled and cancelled(): raise asyncio.CancelledError()
            body = {'filter': {'status': 'ALL'}, 'limit': 100, 'last_id': last_id, 'sort_dir': 'DESC'}
            result = await guard.request('ozon', 'questions', headers=self.headers(store, token), body=body)
            items = result.get('questions') or (result.get('result') or {}).get('questions') or []
            for item in items:
                status = str(item.get('status') or '').upper()
                if upsert_market_question(db, store, item.get('id') or item.get('question_id'), item.get('sku'), item.get('product_name'), item.get('text'), item.get('published_at') or item.get('created_at'), status in ('PROCESSED', 'ANSWERED')):
                    total += 1
            db.commit()
            if between_pages: await between_pages()
            if progress: progress(min(95, 5 + total // 100))
            if len(items) < 100: break
            next_id = str(result.get('last_id') or (result.get('result') or {}).get('last_id') or '')
            if not next_id or next_id in seen: break
            seen.add(next_id)
            last_id = next_id
            if checkpoint: checkpoint({'last_id': last_id, 'processed': total})
        return total


class YandexProvider:
    def target(self, store, operation):
        business_id = str((store.config or {}).get('business_id', '')).strip()
        if not business_id.isdigit(): raise ValueError('Для Яндекс Маркета укажите числовой Business ID')
        if operation == 'products':
            return f'https://api.partner.market.yandex.ru/v2/businesses/{business_id}/offer-mappings'
        if operation in ('questions', 'question_publish'):
            suffix = '/update' if operation == 'question_publish' else ''
            return f'https://api.partner.market.yandex.ru/v1/businesses/{business_id}/goods-questions{suffix}'
        suffix = '/comments/update' if operation == 'publish' else ''
        return f'https://api.partner.market.yandex.ru/v2/businesses/{business_id}/goods-feedback{suffix}'

    async def test(self, db, store_id):
        store, token = store_credentials(db, store_id)
        await guard.request('yandex', 'reviews', headers={'Api-Key': token}, url=self.target(store, 'reviews'), params={'limit': 1}, body={'reactionStatus':'ALL'})
        return True

    async def sync_products(self, db, store_id, progress=None, cancelled=None, resume=None, checkpoint=None, between_pages=None, _offer_ids=None, _archived=False, _detail_warnings=None):
        from .yandex_content import card_characteristics
        store, token = store_credentials(db, store_id)
        category_cache = {}
        detail_warnings = _detail_warnings if _detail_warnings is not None else []
        total, page = int((resume or {}).get('processed', 0)), str((resume or {}).get('page', ''))
        _archived = _archived or (resume or {}).get('phase') == 'archived'

        async def resolve_missing(count):
            prefix = f'yandex:{store.id}:'
            missing = list(db.scalars(select(Product.id).where(
                Product.id.startswith(prefix),
                Product.name == func.substr(Product.id, len(prefix) + 1),
                Product.brand == '',
            ).order_by(Product.id)))
            if checkpoint: checkpoint({'page': '', 'processed': count, 'phase': 'missing'})
            for offset in range(0, len(missing), 100):
                if cancelled and cancelled(): raise asyncio.CancelledError()
                count += await self.sync_products(db, store_id, cancelled=cancelled,
                    _offer_ids=[pid[len(prefix):] for pid in missing[offset:offset + 100]],
                    _detail_warnings=detail_warnings, between_pages=between_pages)
                if checkpoint: checkpoint({'page': '', 'processed': count, 'phase': 'missing'})
                if between_pages: await between_pages()
                await asyncio.sleep(0)
            return count

        if (resume or {}).get('phase') == 'missing' and _offer_ids is None:
            return await resolve_missing(total)
        seen = {page} if page else set()
        for _ in range(1000):
            if cancelled and cancelled():
                raise asyncio.CancelledError()
            params = {} if _offer_ids is not None else {'limit': 100}
            if page and _offer_ids is None: params['pageToken'] = page
            try:
                options = {'body': {'offerIds': _offer_ids}} if _offer_ids is not None else ({'body': {'archived': True}} if _archived else {})
                result = await guard.request('yandex', 'products', headers={'Api-Key': token}, url=self.target(store, 'products'), params=params, **options)
            except ValueError as error:
                if 'HTTP 403' in str(error):
                    raise ValueError('Яндекс Маркет запретил загрузку каталога. Проверьте Business ID и доступ API-ключа «Просмотр товаров и карточек».') from None
                raise
            payload = result.get('result')
            if result.get('status') == 'ERROR' or not isinstance(payload, dict) or not isinstance(payload.get('offerMappings'), list):
                raise ValueError('Яндекс Маркет не вернул каталог товаров. Повторите синхронизацию позже.')
            if _offer_ids is not None and any(str((item.get('offer') or {}).get('offerId') or '') not in _offer_ids for item in payload['offerMappings']):
                raise ValueError('Яндекс Маркет вернул другой артикул при точном поиске товара')
            details = await card_characteristics(store, token, payload['offerMappings'], category_cache, cancelled, detail_warnings)
            set_setting(db, 'yandex-catalog-warning:' + store.id, ('Не удалось получить справочники дополнительных характеристик для категорий: ' + ', '.join(detail_warnings) + '. Основные карточки загружены; прежние проверенные характеристики сохранены.') if detail_warnings else '')
            for item in payload['offerMappings']:
                offer, mapping = item.get('offer') or {}, item.get('mapping') or {}
                sku = str(offer.get('offerId') or '').strip()
                if not sku:
                    raise ValueError('В каталоге Яндекс Маркета получен товар без SKU')
                pid = f'yandex:{store.id}:{sku}'
                product = db.get(Product, pid) or Product(id=pid, marketplace='yandex')
                product.name = str(offer.get('name') or mapping.get('marketSkuName') or sku)
                product.brand = str(offer.get('vendor') or product.brand or '')
                product.category = str(mapping.get('marketCategoryName') or product.category or '')
                product.part_number = str(offer.get('vendorCode') or product.part_number or '')
                pictures = [url for url in offer.get('pictures') or [] if isinstance(url, str) and url.startswith('https://')]
                product.image_url = pictures[0] if pictures else product.image_url or ''
                facts = [fact for fact in product.facts or [] if fact.get('imported_from') != 'yandex-catalog']
                if sku in details:
                    facts = [fact for fact in facts if fact.get('imported_from') != 'yandex-parameters'] + details[sku]
                description = html.unescape(str(offer.get('description') or '')).strip()
                features = []
                if product.part_number: features.append('Артикул производителя: ' + product.part_number)
                countries = offer.get('manufacturerCountries') or []
                if countries: features.append('Страна производства: ' + ', '.join(str(value) for value in countries))
                barcodes = offer.get('barcodes') or []
                if barcodes: features.append('Штрихкоды: ' + ', '.join(str(value) for value in barcodes))
                dimensions = offer.get('weightDimensions') or {}
                if dimensions.get('weight') is not None: features.append('Масса с упаковкой, кг: ' + str(dimensions['weight']))
                if all(dimensions.get(key) is not None for key in ('length', 'width', 'height')):
                    features.append('Габариты упаковки, см: ' + ' × '.join(str(dimensions[key]) for key in ('length', 'width', 'height')))
                for field, content in (('description', description), ('features', '\n'.join(features))):
                    if content:
                        facts.append({'text': content[:8000], 'source': 'Карточка товара', 'source_label': 'Карточка Яндекс Маркета',
                                      'kind': 'feature', 'verification_status': 'VERIFIED', 'source_type': 'marketplace',
                                      'imported_from': 'yandex-catalog', 'catalog_field': field, 'verified_at': now()})
                product.facts = facts
                attach_verified_instructions(db, product)
                db.add(product)
                total += 1
                if total % 20 == 0:
                    await asyncio.sleep(0)
            db.commit()
            if progress: progress(min(95, 5 + total // 100))
            page = str((payload.get('paging') or {}).get('nextPageToken') or '')
            if _offer_ids is not None: return total
            if not page:
                if _archived:
                    return await resolve_missing(total)
                if checkpoint: checkpoint({'page': '', 'processed': total, 'phase': 'archived'})
                return await self.sync_products(db, store_id, progress=progress, cancelled=cancelled,
                    resume={'processed': total, 'phase': 'archived'}, checkpoint=checkpoint,
                    between_pages=between_pages, _archived=True, _detail_warnings=detail_warnings)
            if page in seen:
                raise ValueError('Яндекс Маркет вернул повторную страницу каталога; прогресс сохранён')
            seen.add(page)
            if checkpoint: checkpoint({'page': page, 'processed': total, 'phase': 'archived' if _archived else 'active'})
            if between_pages: await between_pages()
            await asyncio.sleep(0.65)  # Keep below the catalog API's 100 requests/minute.
        raise ValueError('Каталог слишком большой для одного запуска; продолжите синхронизацию с сохранённого прогресса')

    async def sync_reviews(self, db, store_id, progress=None, cancelled=None, resume=None, checkpoint=None, between_pages=None):
        store, token = store_credentials(db, store_id)
        total, page = int((resume or {}).get('processed', 0)), str((resume or {}).get('page', ''))
        for _ in range(1000):
            if cancelled and cancelled(): raise asyncio.CancelledError()
            params = {'limit': 50}
            if page: params['pageToken'] = page
            result = await guard.request('yandex', 'reviews', headers={'Api-Key': token}, url=self.target(store, 'reviews'), params=params, body={'reactionStatus':'ALL'})
            payload = result.get('result', {})
            items = payload.get('feedbacks') or []
            for item in items:
                description = item.get('description') or {}
                identifiers = item.get('identifiers') or {}
                content = '\n'.join(str(description.get(k) or '') for k in ('advantages','disadvantages','comment')).strip()
                statistics = item.get('statistics') or {}
                upsert_market_review(db, store, item.get('feedbackId'), identifiers.get('offerId') or 'unknown', identifiers.get('offerId'), '', statistics.get('rating'), content, item.get('createdAt'), bool(statistics.get('commentsCount')))
                total += 1
            db.commit()
            if progress: progress(min(95, 5 + total // 50))
            page = str((payload.get('paging') or {}).get('nextPageToken') or '')
            if not page: break
            if checkpoint: checkpoint({'page': page, 'processed': total})
            # The page and its resume token are committed before another job
            # uses its own session. Keep question imports responsive while a
            # large review history is loading, without bypassing worker locks.
            if between_pages:
                await between_pages()
        return total

    async def publish(self, db, review, text):
        store, token = store_credentials(db, review.wb_account_id)
        if review.marketplace == 'yandex_question':
            question_id = review.wb_review_id.removeprefix('question:')
            if not question_id.isdigit(): raise ValueError('Некорректный идентификатор вопроса Яндекс Маркета')
            current = await guard.request('yandex', 'questions', headers={'Api-Key': token}, url=self.target(store, 'questions'), params={'limit': 1}, body={'questionIds': [int(question_id)], 'needAnswer': True})
            questions = (current.get('result') or {}).get('questions') or []
            if not questions or str((questions[0].get('questionIdentifiers') or {}).get('id')) != question_id:
                raise ValueError('Яндекс Маркет не подтвердил существование вопроса')
            await guard.request('yandex', 'question_publish', headers={'Api-Key': token}, url=self.target(store, 'question_publish'), body={'operationType': 'CREATE', 'parentEntityId': {'id': int(question_id), 'type': 'QUESTION'}, 'text': text})
            return
        if not review.wb_review_id.isdigit(): raise ValueError('Некорректный идентификатор отзыва Яндекс Маркета')
        await guard.request('yandex', 'publish', headers={'Api-Key': token}, url=self.target(store, 'publish'), body={'feedbackId': int(review.wb_review_id), 'comment': {'text': text[:4096]}})

    async def sync_questions(self, db, store_id, progress=None, cancelled=None, resume=None, checkpoint=None, between_pages=None):
        store, token = store_credentials(db, store_id)
        total = int((resume or {}).get('processed', 0))
        # The API exposes needAnswer as a filter, not a property of each question.
        # Read both views so answered status is never guessed from a missing field.
        for need_answer in (False, True):
            if (resume or {}).get('need_answer') is True and not need_answer:
                continue
            page, seen = (str((resume or {}).get('page', '')) if (resume or {}).get('need_answer') == need_answer else ''), set()
            for _ in range(1000):
                if cancelled and cancelled(): raise asyncio.CancelledError()
                params = {'limit': 50}
                if page: params['pageToken'] = page
                result = await guard.request('yandex', 'questions', headers={'Api-Key': token}, url=self.target(store, 'questions'), params=params, body={'needAnswer': need_answer, 'sort': 'CREATED_AT_DESC'})
                payload = result.get('result') or {}
                items = payload.get('questions') or []
                for item in items:
                    ids = item.get('questionIdentifiers') or {}
                    if upsert_market_question(db, store, ids.get('id'), ids.get('offerId'), ids.get('offerId'), item.get('text'), item.get('createdAt'), not need_answer):
                        total += 1 if not need_answer else 0
                db.commit()
                if between_pages: await between_pages()
                if progress: progress(min(95, 5 + total // 50))
                page = str((payload.get('paging') or {}).get('nextPageToken') or '')
                if not page or page in seen: break
                seen.add(page)
                if checkpoint: checkpoint({'need_answer': need_answer, 'page': page, 'processed': total})
            if checkpoint: checkpoint({'need_answer': True, 'page': '', 'processed': total})
        return total


ozon, yandex = OzonProvider(), YandexProvider()


def provider_for(store):
    return {'wb': wb, 'ozon': ozon, 'yandex': yandex}.get(store.provider)

