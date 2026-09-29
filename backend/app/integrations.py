import asyncio
import html
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit
from sqlalchemy import select
from .config import MODEL
from .db import Account, Product, Review, Session, now, setting
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

    async def publish(self, db, review, text):
        token = wb_token(db, review.wb_account_id)
        current = await guard.request('wb', 'review', token=token, params={'id': review.wb_review_id})
        if current.get('error') or current.get('data', {}).get('id') != review.wb_review_id:
            raise ValueError('WB не подтвердил существование выбранного отзыва')
        if current.get('data', {}).get('answer'):
            raise ValueError('У отзыва уже есть ответ в WB')
        await guard.request('wb', 'publish', token=token, body={'id': review.wb_review_id, 'text': text})


wb = WildberriesProvider()
