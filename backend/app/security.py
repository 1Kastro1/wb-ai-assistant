"""No arbitrary destinations or credentials are exposed to the assistant."""
import base64
import ctypes
import hashlib
import os
import re
import time
from urllib.parse import urlsplit
import httpx
from .db import Session, Audit, setting


def redact(text):
    text = re.sub(r'(?i)(authorization|bearer|token|api_key|password|cookie|session|secret)\s*[:= ]\s*[^\s,;]+', r'\1=[REDACTED]', str(text))
    return re.sub(r'\b[A-HJ-NPR-Z0-9]{17}\b', '[VIN скрыт]', text, flags=re.I)


class Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(raw: bytes, encrypt=True) -> bytes:
    if os.name != 'nt':
        raise ValueError('Хранение токена поддерживается в Windows через DPAPI')
    buf = ctypes.create_string_buffer(raw)
    src = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    fn.restype = ctypes.c_int
    if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise ValueError('Ошибка Windows DPAPI')
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(dst.data)


def encrypt_secret(value):
    if os.name != 'nt':
        return 'fernet:' + container_cipher().encrypt(value.encode()).decode()
    return base64.b64encode(dpapi(value.encode())).decode()


def decrypt_secret(value):
    if os.name != 'nt':
        if not value.startswith('fernet:'):
            raise ValueError('Токен DPAPI необходимо подключить заново на Windows')
        return container_cipher().decrypt(value[7:].encode()).decode()
    return dpapi(base64.b64decode(value), False).decode()


def container_cipher():
    from pathlib import Path
    from cryptography.fernet import Fernet
    # Linux/Docker requires a separately mounted key; no plaintext fallback.
    path = os.environ.get('WB_SECRET_KEY_FILE')
    if not path or not Path(path).is_file():
        raise ValueError('Для контейнера нужен отдельный смонтированный ключ шифрования')
    return Fernet(Path(path).read_bytes().strip())


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class EgressGuard:
    # Exact operation-to-method-to-URL binding; POST is not necessarily a write.
    ENDPOINTS = {
        ('ollama', 'health'): ('GET', 'http://127.0.0.1:11434/api/tags'),
        ('ollama', 'chat'): ('POST', 'http://127.0.0.1:11434/api/chat'),
        ('wb', 'reviews'): ('GET', 'https://feedbacks-api.wildberries.ru/api/v1/feedbacks'),
        ('wb', 'review'): ('GET', 'https://feedbacks-api.wildberries.ru/api/v1/feedback'),
        ('wb', 'products'): ('POST', 'https://content-api.wildberries.ru/content/v2/get/cards/list'),
        ('wb', 'publish'): ('POST', 'https://feedbacks-api.wildberries.ru/api/v1/feedbacks/answer'),
        ('ozon', 'reviews'): ('POST', 'https://api-seller.ozon.ru/v2/review/list'),
        ('ozon', 'review'): ('POST', 'https://api-seller.ozon.ru/v2/review/info'),
        ('ozon', 'publish'): ('POST', 'https://api-seller.ozon.ru/v1/review/comment/create'),
        ('yandex', 'reviews'): ('POST', 'https://api.partner.market.yandex.ru/v2/businesses/{business_id}/goods-feedback'),
        ('yandex', 'publish'): ('POST', 'https://api.partner.market.yandex.ru/v2/businesses/{business_id}/goods-feedback/comments/update'),
        ('telegram', 'send'): ('POST', 'https://api.telegram.org/bot{token}/sendMessage'),
        ('web', 'search'): ('GET', 'https://html.duckduckgo.com/html/'),
        ('tavily', 'search'): ('POST', 'https://api.tavily.com/search'),
    }
    if os.environ.get('WB_CONTAINER') == '1':
        ENDPOINTS[('ollama','health')] = ('GET','http://ollama:11434/api/tags')
        ENDPOINTS[('ollama','chat')] = ('POST','http://ollama:11434/api/chat')

    def __init__(self, transport=None):
        self.transport = transport

    def validate(self, provider, operation, url=None):
        endpoint = self.ENDPOINTS.get((provider, operation))
        if not endpoint or (url is not None and url != endpoint[1]):
            raise ValueError('Соединение заблокировано: неизвестный провайдер или операция')
        if operation == 'publish':
            from .config import REAL_PUBLISH
            with Session() as db:
                if setting(db, 'safe_mode', False) or setting(db, 'test_mode', True) or not REAL_PUBLISH:
                    raise ValueError('WB WRITE заблокирован режимом безопасности')
        return endpoint

    async def request(self, provider, operation, *, token=None, params=None, body=None, headers=None, url=None, return_text=False):
        start = time.monotonic()
        status, host = 'BLOCKED', 'blocked'
        try:
            method, template = self.validate(provider, operation)
            url = url or template
            if '{' not in template and url != template:
                raise ValueError('Соединение заблокировано: адрес не разрешён')
            if provider == 'yandex' and not re.fullmatch(r'https://api\.partner\.market\.yandex\.ru/v2/businesses/[0-9]+/goods-feedback(?:/comments/update)?', url):
                raise ValueError('Соединение заблокировано: неверный адрес Яндекс Маркета')
            if provider == 'telegram' and not re.fullmatch(r'https://api\.telegram\.org/bot[^/]+/sendMessage', url):
                raise ValueError('Соединение заблокировано: неверный адрес Telegram')
            host = urlsplit(url).hostname
            # Ignore environment proxies; never follow redirects with a secret.
            async with httpx.AsyncClient(timeout=120 if provider == 'ollama' else 30, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                attempts = 1 if operation == 'publish' else 3
                for attempt in range(attempts):
                    safe_headers = dict(headers or {})
                    if token:
                        safe_headers['Authorization'] = token
                    response = await client.request(method, url, params=params, json=body, headers=safe_headers)
                    status = str(response.status_code)
                    if response.status_code in (429, 502, 503, 504) and attempt + 1 < attempts:
                        import asyncio
                        await asyncio.sleep(min(4, 2 ** attempt))
                        continue
                    break
                if not response.is_success:
                    raise ValueError(f'{provider}: HTTP {response.status_code}. Проверьте доступ и повторите позже.')
                if return_text:
                    return response.text
                return response.json() if response.content else {}
        except httpx.HTTPError:
            status = 'NETWORK_ERROR'
            raise ValueError('Сервис недоступен; данные не отправлены в другие сервисы') from None
        finally:
            with Session() as db:
                db.add(Audit(provider=provider if provider in ('wb', 'ozon', 'yandex', 'telegram', 'ollama', 'web', 'tavily') else 'unknown', host=host, operation=operation if (provider, operation) in self.ENDPOINTS else 'blocked', status=status, duration_ms=int((time.monotonic() - start) * 1000)))
                db.commit()


guard = EgressGuard()
