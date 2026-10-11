import asyncio
import html
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit
from sqlalchemy import select
from .config import MODEL
from .db import Account, Draft, Product, Review, StoreProfile, Session, now, setting
from .safety import classify, mask_vin
from .security import guard, decrypt_secret


class OllamaProvider:
    async def health_check(self):
        return await guard.request('ollama', 'health')

    async def list_models(self):
        return (await self.health_check()).get('models', [])

    async def chat(self, messages, schema=None):
        from .model_router import ModelRouter
        messages = [dict(message) for message in messages]
        if messages and messages[-1].get('role') == 'user':
            # Qwen 3 understands this template command even when an older Ollama build
            # ignores the API-level `think: false` flag.
            messages[-1]['content'] = messages[-1].get('content', '') + '\n/no_think'
        body = {**ModelRouter().choose('FAST' if schema else 'STANDARD'), 'messages': messages, 'stream': False, 'think': False}
        if schema:
            body['format'] = schema
        result = await guard.request('ollama', 'chat', body=body)
        content = result['message']['content']
        # Some installed Qwen templates include reasoning in content despite think=false.
        if '</think>' in content:
            content = content.rsplit('</think>', 1)[-1]
        elif '<think>' in content:
            raise ValueError('Локальная модель не завершила ответ. Повторите запрос.')
        return content.strip()

    async def structured(self, messages, schema):
        return json.loads(await self.chat(messages, schema))

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

    async def search_instructions(self, product, db=None):
        query = f'"{product.brand}" "{product.name}" инструкция применения'
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
        page = await guard.request('web', 'search', params={'q': query, 'kl': 'ru-ru'}, return_text=True)
        lowered = page.lower()
        if 'please complete the following challenge' in lowered or 'bots use duckduckgo too' in lowered:
            raise ValueError('Интернет-поиск инструкций временно заблокирован поисковым сервисом')
        links = re.findall(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.I | re.S)
        snippets = re.findall(r'<(?:a|div)[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>', page, re.I | re.S)
        wanted = {x.lower() for x in re.findall(r'[а-яёa-z0-9]{4,}', product.name + ' ' + product.brand, re.I)}
        raw = []
        for index, (href, title) in enumerate(links[:10]):
            parsed = urlsplit(html.unescape(href))
            if parsed.netloc.endswith('duckduckgo.com'):
                href = unquote(parse_qs(parsed.query).get('uddg', [''])[0])
            snippet = self.clean(snippets[index]) if index < len(snippets) else ''
            title = self.clean(title)
            raw.append({'title': title, 'snippet': snippet, 'url': href})
        return self._normalized_results(product, raw)

    def _normalized_results(self, product, items):
        wanted = {x.lower() for x in re.findall(r'[а-яёa-z0-9]{4,}', product.name + ' ' + product.brand, re.I)}
        results = []
        for item in items:
            source = urlsplit(item.get('url', ''))
            if source.scheme not in ('http', 'https') or not source.netloc:
                continue
            title, snippet = self.clean(item.get('title', '')), self.clean(item.get('snippet', ''))
            found = {x.lower() for x in re.findall(r'[а-яёa-z0-9]{4,}', title + ' ' + snippet, re.I)}
            score = len(wanted & found)
            if score >= min(3, max(1, len(wanted))):
                results.append({'title': title[:300], 'snippet': snippet[:1200], 'url': item['url'], 'host': source.netloc.lower(), 'match_score': score})
        return results[:5]


web_research = WebResearchProvider()


def wb_token(db, account_id=None):
    account_id = account_id or setting(db, 'active_store', 'owner')
    row = db.get(Account, account_id)
    if not row:
        raise ValueError('Сначала сохраните WB API Token в настройках')
    return decrypt_secret(row.encrypted_token)


class WildberriesProvider:
    async def sync_products(self, db, account_id=None, progress=None, cancelled=None):
        token = wb_token(db, account_id)
        cursor, total = {'limit': 100}, 0
        seen = set()
        for _ in range(1000):
            if cancelled and cancelled():
                raise asyncio.CancelledError()
            result = await guard.request('wb', 'products', token=token, body={'settings': {'sort': {'ascending': True}, 'filter': {'withPhoto': -1}, 'cursor': cursor}})
            for item in result.get('cards', []):
                pid = str(item['nmID'])
                product = db.get(Product, pid) or Product(id=pid)
                product.name, product.brand, product.category = item.get('title', ''), item.get('brand', ''), item.get('subjectName', '')
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
                # vendorCode is seller SKU, not verified manufacturer part number.
                db.add(product)
                total += 1
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
            await asyncio.sleep(0.65)
        raise ValueError('Достигнут лимит страниц синхронизации')

    async def sync_reviews(self, db, account_id=None, progress=None, cancelled=None):
        account_id = account_id or setting(db, 'active_store', 'owner')
        token, total = wb_token(db, account_id), 0
        for answered in (False, True):
            for skip in range(0, 200000, 5000):
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
                db.commit()
                if progress:
                    progress(min(95, 5 + total // 2500))
                if len(items) < 5000:
                    break
                await asyncio.sleep(0.4)
        return total

    async def sync_questions(self, db, account_id=None, progress=None, cancelled=None):
        account_id = account_id or setting(db, 'active_store', 'owner')
        token, total = wb_token(db, account_id), 0
        for answered in (False, True):
            for skip in range(0, 200000, 5000):
                if cancelled and cancelled():
                    raise asyncio.CancelledError()
                result = await guard.request('wb', 'questions', token=token, params={'isAnswered': str(answered).lower(), 'take': 5000, 'skip': skip, 'order': 'dateDesc'})
                if result.get('error'):
                    raise ValueError('WB сообщил об ошибке получения вопросов')
                items = result.get('data', {}).get('questions', [])
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
                    row.text = mask_vin(str(item.get('text') or '').strip())
                    row.created_at = str(item.get('createdDate') or now())
                    row.is_answered = bool(answer and answer.get('text'))
                    row.existing_answer = mask_vin(str(answer.get('text') or ''))
                    row.status = 'published' if row.is_answered else ('draft_ready' if db.scalar(select(Draft).where(Draft.review_id == row.id)) else 'unanswered')
                    row.marketplace = 'wb_question'
                    db.add(row)
                    total += 1
                db.commit()
                if progress:
                    progress(min(95, 5 + total // 2500))
                if len(items) < 5000:
                    break
                await asyncio.sleep(0.4)
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
    product = db.get(Product, product_id) or Product(id=product_id, name=product_name or str(product_external_id or 'Товар'), brand='', category='', marketplace=store.provider)
    db.add(product)
    db.flush()
    stored_id = f'question:{external_id}'
    row = db.scalar(select(Review).where(Review.wb_account_id == store.id, Review.wb_review_id == stored_id))
    if not row:
        row = Review(id=f'{store.id}:{stored_id}', wb_account_id=store.id, wb_review_id=stored_id, product_id=product_id, rating=0, status='unanswered', marketplace=f'{store.provider}_question')
    row.text = mask_vin(str(text or '').strip())
    row.created_at = str(created_at or now())
    row.is_answered = bool(answered)
    row.status = 'published' if answered else ('draft_ready' if db.scalar(select(Draft).where(Draft.review_id == row.id)) else 'unanswered')
    db.add(row)
    return row


class OzonProvider:
    def headers(self, store, token):
        client_id = str((store.config or {}).get('client_id', '')).strip()
        if not client_id:
            raise ValueError('Для Ozon укажите Client-Id')
        return {'Client-Id': client_id, 'Api-Key': token}

    async def test(self, db, store_id):
        store, token = store_credentials(db, store_id)
        await guard.request('ozon', 'reviews', headers=self.headers(store, token), body={'limit': 1, 'sort_dir': 'DESC'})
        return True

    async def sync_reviews(self, db, store_id, progress=None, cancelled=None):
        store, token = store_credentials(db, store_id)
        total, last_id = 0, ''
        for _ in range(1000):
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
            if progress: progress(min(95, 5 + total // 100))
            if len(items) < 100: break
            last_id = str(result.get('last_id') or items[-1].get('id') or '')
            if not last_id: break
        return total

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

    async def sync_questions(self, db, store_id, progress=None, cancelled=None):
        store, token = store_credentials(db, store_id)
        total, last_id, seen = 0, '', set()
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
            if progress: progress(min(95, 5 + total // 100))
            if len(items) < 100: break
            next_id = str(result.get('last_id') or (result.get('result') or {}).get('last_id') or '')
            if not next_id or next_id in seen: break
            seen.add(next_id)
            last_id = next_id
        return total


class YandexProvider:
    def target(self, store, operation):
        business_id = str((store.config or {}).get('business_id', '')).strip()
        if not business_id.isdigit(): raise ValueError('Для Яндекс Маркета укажите числовой Business ID')
        if operation in ('questions', 'question_publish'):
            suffix = '/update' if operation == 'question_publish' else ''
            return f'https://api.partner.market.yandex.ru/v1/businesses/{business_id}/goods-questions{suffix}'
        suffix = '/comments/update' if operation == 'publish' else ''
        return f'https://api.partner.market.yandex.ru/v2/businesses/{business_id}/goods-feedback{suffix}'

    async def test(self, db, store_id):
        store, token = store_credentials(db, store_id)
        await guard.request('yandex', 'reviews', headers={'Api-Key': token}, url=self.target(store, 'reviews'), params={'limit': 1}, body={'reactionStatus':'ALL'})
        return True

    async def sync_reviews(self, db, store_id, progress=None, cancelled=None):
        store, token = store_credentials(db, store_id)
        total, page = 0, ''
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

    async def sync_questions(self, db, store_id, progress=None, cancelled=None):
        store, token = store_credentials(db, store_id)
        total = 0
        # The API exposes needAnswer as a filter, not a property of each question.
        # Read both views so answered status is never guessed from a missing field.
        for need_answer in (False, True):
            page, seen = '', set()
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
                if progress: progress(min(95, 5 + total // 50))
                page = str((payload.get('paging') or {}).get('nextPageToken') or '')
                if not page or page in seen: break
                seen.add(page)
        return total


ozon, yandex = OzonProvider(), YandexProvider()


def provider_for(store):
    return {'wb': wb, 'ozon': ozon, 'yandex': yandex}.get(store.provider)
