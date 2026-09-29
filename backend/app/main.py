import secrets
import time
import hashlib
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from fastapi import FastAPI, Depends, Request, Response, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, delete, text, func
from .db import *
from .config import ORIGIN, DATA, DATABASE, MODEL, REAL_PUBLISH
from .security import digest, encrypt_secret, guard
from .integrations import ollama, wb, wb_token
from .services import reviews_search, reviews_page, analytics, quality_report, generate_draft, edit_draft, propose_publish, confirm_action
from .assistant import assistant
from .compatibility import import_catalog, validate_vin, check_compatibility
from .safety import mask_vin
from .maintenance import backup, portable_backup, portable_restore
from . import voice


@asynccontextmanager
async def lifespan(app):
    from alembic.config import Config
    from alembic import command
    cfg = Config(str(Path(__file__).parent.parent / 'alembic.ini'))
    cfg.set_main_option('script_location', str(Path(__file__).parent.parent / 'migrations'))
    command.upgrade(cfg, 'head')
    with Session() as db:
        db.merge(StoreProfile(id='owner', name='Мой магазин'))
        if not setting(db, 'active_store'):
            set_setting(db, 'active_store', 'owner')
        for job in db.scalars(select(Job).where(Job.status.in_(['queued','running']))):
            job.status, job.result = 'interrupted', {'message':'Приложение перезапущено; повторите синхронизацию'}
        for job in db.scalars(select(Job).where(Job.status == 'completed', Job.progress < 100)):
            job.progress = 100
        for action in db.scalars(select(Action).where(Action.status == 'executing')):
            action.status = 'needs_review'
            action.result = {'message':'Работа была прервана. Сверьте результат с WB до дальнейших действий.'}
        for publication in db.scalars(select(Publication).where(Publication.status == 'in_flight')):
            publication.status = 'uncertain'
            review = db.get(Review,publication.review_id)
            review.status = 'manual_review'
        db.commit()
    scheduler_task = asyncio.create_task(scheduler_loop())
    yield
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title='WB AI Assistant', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.state.maintenance = False
def allowed_origin(origin):
    return origin == ORIGIN


app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN], allow_credentials=True, allow_methods=['GET', 'POST', 'PATCH', 'DELETE'], allow_headers=['Content-Type', 'X-CSRF-Token'])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def boundary(request, call_next):
    if app.state.maintenance and request.url.path != '/portable/restore':
        return JSONResponse({'detail': 'Выполняется восстановление данных. Повторите запрос после завершения.'}, 503)
    origin = request.headers.get('origin')
    if origin and not allowed_origin(origin):
        return JSONResponse({'detail': 'Недопустимый Origin'}, 403)
    upload_limit = 1024 * 1024 * 1024 if request.url.path == '/portable/restore' else 12 * 1024 * 1024
    if int(request.headers.get('content-length', '0')) > upload_limit:
        return JSONResponse({'detail': 'Слишком большой запрос'}, 413)
    if request.method in ('POST', 'PATCH', 'DELETE') and not allowed_origin(origin):
        return JSONResponse({'detail': 'Нужен локальный Origin'}, 403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': str(exc)}, 400)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's default errors include the raw submitted input (possibly tokens).
    return JSONResponse({'detail': 'Проверьте формат и обязательные поля запроса'}, 422)


def database():
    with Session() as db:
        yield db


DB = Annotated[object, Depends(database)]


def authorize(request: Request):
    cookie = request.cookies.get('wb_session', '')
    with Session() as db:
        session = db.get(LoginSession, digest(cookie)) if cookie else None
        if not session or session.expires < time.time():
            raise HTTPException(401, 'Войдите в приложение')
        if request.method not in ('GET', 'HEAD') and not secrets.compare_digest(session.csrf, request.headers.get('x-csrf-token', '')):
            raise HTTPException(403, 'Неверный CSRF-токен')
        return session.csrf


AUTH = Annotated[str, Depends(authorize)]


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Password(Payload):
    password: str = Field(min_length=1, max_length=1024)


class SetupPassword(Payload):
    password: str = Field(min_length=12, max_length=1024)
    confirmation: str = Field(min_length=12, max_length=1024)


login_attempts = []
login_lock = asyncio.Lock()
setup_lock = asyncio.Lock()


@app.get('/health')
def health(db: DB):
    db.execute(text('SELECT 1'))
    return {'status': 'ok', 'database': 'ok'}


@app.post('/auth/login')
async def login(body: Password, response: Response, request: Request, db: DB):
    async with login_lock:
        current = time.time()
        login_attempts[:] = [t for t in login_attempts if current - t < 300]
        if len(login_attempts) >= 5:
            raise HTTPException(429, 'Слишком много попыток. Повторите через 5 минут')
        value = setting(db, 'password_hash')
        if not value:
            raise HTTPException(409, 'Первый пароль создаётся локально: запустите setup.bat')
        try:
            PasswordHasher().verify(value, body.password)
        except (VerificationError, InvalidHashError):
            login_attempts.append(current)
            raise HTTPException(401, 'Неверный пароль') from None
        login_attempts.clear()
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        db.execute(delete(LoginSession).where(LoginSession.expires < current))
        db.add(LoginSession(id=digest(token), expires=int(current + 8 * 3600), csrf=csrf))
        db.commit()
        response.set_cookie('wb_session', token, httponly=True, secure=request.headers.get('origin', '').startswith('https://'), samesite='strict', max_age=8 * 3600, path='/')
        return {'csrf': csrf}


@app.get('/auth/status')
def auth_status(db: DB):
    return {'initialized': bool(setting(db, 'password_hash'))}


@app.post('/auth/setup')
async def auth_setup(body: SetupPassword, response: Response, request: Request, db: DB):
    if body.password != body.confirmation:
        raise ValueError('Пароли не совпадают')
    async with setup_lock:
        db.rollback()
        db.execute(text('BEGIN IMMEDIATE'))
        if setting(db, 'password_hash'):
            raise HTTPException(409, 'Пароль владельца уже создан')
        set_setting(db, 'password_hash', PasswordHasher().hash(body.password))
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        db.add(LoginSession(id=digest(token), expires=int(time.time() + 8 * 3600), csrf=csrf))
        db.commit()
        response.set_cookie('wb_session', token, httponly=True, secure=request.headers.get('origin', '').startswith('https://'), samesite='strict', max_age=8 * 3600, path='/')
        return {'csrf': csrf}


@app.get('/auth/session')
def session(auth: AUTH):
    return {'csrf': auth}


@app.post('/auth/logout')
def logout(request: Request, response: Response, auth: AUTH, db: DB):
    db.execute(delete(LoginSession).where(LoginSession.id == digest(request.cookies['wb_session'])))
    db.commit()
    response.delete_cookie('wb_session')
    return {'ok': True}


@app.get('/security')
def security(auth: AUTH, db: DB):
    audit = db.scalars(select(Audit).order_by(Audit.id.desc()).limit(30)).all()
    active = setting(db, 'active_store', 'owner')
    return {'privacy_mode': 'LOCAL_FIRST', 'safe_mode': setting(db, 'safe_mode', False), 'test_mode': setting(db, 'test_mode', True), 'cloud_ai': False, 'web_research': True, 'search_provider': setting(db, 'search_provider', 'duckduckgo'), 'search_configured': bool(db.get(Account, 'search:tavily')), 'telemetry': False, 'database': 'LOCAL', 'token': 'ENCRYPTED (Windows DPAPI)' if db.get(Account, active) else 'NOT_CONNECTED', 'active_store': active, 'backend': '127.0.0.1', 'model': MODEL, 'real_publish_enabled': REAL_PUBLISH, 'audit': [public(a) for a in audit], 'backups': sorted(p.name for p in (DATA / 'backups').glob('*.db'))[-10:]}


@app.get('/system/status')
def system_status(auth: AUTH, db: DB):
    schedule = setting(db, 'automation_schedule', {})
    latest = {}
    for kind in ('reviews', 'products', 'drafts'):
        job = db.scalar(select(Job).where(Job.kind == kind, Job.status == 'completed').order_by(Job.created_at.desc()))
        latest[kind] = public(job) if job else None
    backups = sorted((DATA / 'backups').glob('*.db'), key=lambda path: path.stat().st_mtime, reverse=True)[:10]
    return {
        'database_mb': round(DATABASE.stat().st_size / 1024 / 1024, 1) if DATABASE.exists() else 0,
        'automation_enabled': bool(schedule.get('enabled')),
        'auto_replies_enabled': bool(schedule.get('enabled') and schedule.get('drafts_enabled')),
        'scheduler': setting(db, 'scheduler_status', {'state': 'waiting'}),
        'latest': latest,
        'active_jobs': db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(['queued', 'running']))) or 0,
        'backups': [{'name': path.name, 'size_mb': round(path.stat().st_size / 1024 / 1024, 1), 'created_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()} for path in backups],
    }


class Modes(Payload):
    safe_mode: bool
    test_mode: bool


class Performance(Payload):
    mode: str


@app.get('/settings/performance')
def performance_get(auth: AUTH, db: DB):
    return {'mode':setting(db,'performance_mode','normal')}


@app.patch('/settings/performance')
def performance_set(body: Performance, auth: AUTH, db: DB):
    from .model_router import ModelRouter
    if body.mode not in ModelRouter.PROFILES:
        raise ValueError('Неизвестный режим производительности')
    set_setting(db,'performance_mode',body.mode)
    db.commit()
    return {'mode':body.mode}


@app.patch('/settings/modes')
def modes(body: Modes, auth: AUTH, db: DB):
    if not body.test_mode and not REAL_PUBLISH:
        raise ValueError('Сначала отдельно разрешите реальную публикацию при запуске')
    set_setting(db, 'safe_mode', body.safe_mode)
    set_setting(db, 'test_mode', body.test_mode)
    db.commit()
    return {'ok': True}


class Token(Payload):
    token: str = Field(min_length=10, max_length=4096)


class SearchProviderInput(Payload):
    provider: str
    api_key: str = Field(default='', max_length=4096)


class StoreInput(Payload):
    name: str = Field(min_length=2, max_length=100)


class ScheduleInput(Payload):
    enabled: bool = False
    reviews_hours: int = Field(default=3, ge=1, le=168)
    products_hours: int = Field(default=24, ge=1, le=720)
    backup_hours: int = Field(default=24, ge=1, le=720)
    drafts_enabled: bool = False
    drafts_hours: int = Field(default=6, ge=1, le=168)
    auto_propose: bool = False
    min_quality: int = Field(default=96, ge=70, le=100)
    max_proposals: int = Field(default=100, ge=1, le=100)
    drafts_days: int = Field(default=7, ge=1, le=90)


@app.post('/settings/wb-token')
def token(body: Token, auth: AUTH, db: DB):
    active = setting(db, 'active_store', 'owner')
    if db.scalar(select(func.count()).select_from(Review).where(Review.wb_account_id == active)):
        raise ValueError('В этом профиле уже есть отзывы. Создайте новый профиль магазина для другого токена')
    db.merge(Account(id=active, encrypted_token=encrypt_secret(body.token)))
    db.commit()
    return {'token': 'ENCRYPTED'}


@app.post('/settings/wb-test')
async def wb_test(auth: AUTH, db: DB):
    await guard.request('wb', 'reviews', token=wb_token(db), params={'isAnswered': 'false', 'take': 1, 'skip': 0})
    return {'ok': True}


@app.get('/settings/search')
def search_settings(auth: AUTH, db: DB):
    provider = setting(db, 'search_provider', 'duckduckgo')
    return {'provider': provider, 'configured': bool(db.get(Account, 'search:tavily')), 'status': 'Надёжный API подключён' if provider == 'tavily' and db.get(Account, 'search:tavily') else 'Резервный поиск без гарантии доступности'}


@app.patch('/settings/search')
def search_settings_update(body: SearchProviderInput, auth: AUTH, db: DB):
    if body.provider not in ('duckduckgo', 'tavily'):
        raise ValueError('Поддерживаются DuckDuckGo и Tavily')
    if body.provider == 'tavily' and not body.api_key and not db.get(Account, 'search:tavily'):
        raise ValueError('Для Tavily нужен API-ключ')
    if body.api_key:
        db.merge(Account(id='search:tavily', encrypted_token=encrypt_secret(body.api_key)))
    set_setting(db, 'search_provider', body.provider)
    db.commit()
    return search_settings(auth, db)


@app.get('/stores')
def stores(auth: AUTH, db: DB):
    active = setting(db, 'active_store', 'owner')
    return {'active': active, 'items': [{**public(s), 'connected': bool(db.get(Account, s.id)), 'reviews': db.scalar(select(func.count()).select_from(Review).where(Review.wb_account_id == s.id)) or 0} for s in db.scalars(select(StoreProfile).order_by(StoreProfile.created_at))]}


@app.post('/stores')
def create_store(body: StoreInput, auth: AUTH, db: DB):
    store = StoreProfile(id='store-' + uuid4().hex[:12], name=body.name.strip())
    db.add(store)
    db.commit()
    return public(store)


@app.post('/stores/{store_id}/activate')
def activate_store(store_id: str, auth: AUTH, db: DB):
    if not db.get(StoreProfile, store_id):
        raise HTTPException(404)
    set_setting(db, 'active_store', store_id)
    db.commit()
    return {'active': store_id}


@app.get('/settings/schedule')
def schedule_get(auth: AUTH, db: DB):
    defaults = {'enabled': False, 'reviews_hours': 3, 'products_hours': 24, 'backup_hours': 24, 'drafts_enabled': False, 'drafts_hours': 6, 'auto_propose': False, 'min_quality': 96, 'max_proposals': 100, 'drafts_days': 7}
    return {**defaults, **setting(db, 'automation_schedule', {})}


@app.patch('/settings/schedule')
def schedule_set(body: ScheduleInput, auth: AUTH, db: DB):
    current = setting(db, 'automation_schedule', {})
    value = {**current, **body.model_dump()}
    if value.get('drafts_enabled'):
        value['drafts_since'] = (datetime.now(timezone.utc) - timedelta(days=int(value.get('drafts_days', 7)))).isoformat()
    set_setting(db, 'automation_schedule', value)
    db.commit()
    return value


def auto_replies_status(db):
    schedule = schedule_get(None, db)
    enabled = bool(schedule.get('enabled') and schedule.get('drafts_enabled'))
    current_job = db.scalar(select(Job).where(Job.kind == 'drafts', Job.status.in_(['queued', 'running'])).order_by(Job.created_at.desc()))
    last_job = db.scalar(select(Job).where(Job.kind == 'drafts', Job.status.not_in(['queued', 'running'])).order_by(Job.created_at.desc()))
    next_run_at = None
    if enabled:
        last_run = float(schedule.get('last_drafts', time.time()))
        next_run_at = int((last_run + max(1, int(schedule.get('drafts_hours', 6))) * 3600) * 1000)
    return {
        'enabled': enabled,
        'state': current_job.status if enabled and current_job else ('active' if enabled else 'paused'),
        'interval_hours': max(1, int(schedule.get('drafts_hours', 6))),
        'next_run_at': next_run_at,
        'current_job': public(current_job) if current_job else None,
        'last_job': public(last_job) if last_job else None,
    }


@app.get('/auto-replies/status')
def get_auto_replies_status(auth: AUTH, db: DB):
    return auto_replies_status(db)


@app.post('/auto-replies/start')
async def start_auto_replies(background: BackgroundTasks, auth: AUTH, db: DB):
    schedule = schedule_get(auth, db)
    since = (datetime.now(timezone.utc) - timedelta(days=int(schedule.get('drafts_days', 7)))).isoformat()
    schedule.update({'enabled': True, 'drafts_enabled': True, 'last_drafts': time.time(), 'drafts_since': since})
    set_setting(db, 'automation_schedule', schedule)
    job = db.scalar(select(Job).where(Job.kind == 'drafts', Job.status.in_(['queued', 'running'])).order_by(Job.created_at.desc()))
    if not job:
        active = setting(db, 'active_store', 'owner')
        job = Job(id=str(uuid4()), kind='drafts', priority=50, result={'account_id': active, 'since': since, 'message': 'Автоответы запущены: подготавливаем новые отзывы'})
        db.add(job)
    db.commit()
    result = auto_replies_status(db)
    result['job'] = public(job)
    background.add_task(run_job_queue)
    return result


@app.post('/auto-replies/pause')
def pause_auto_replies(auth: AUTH, db: DB):
    schedule = schedule_get(auth, db)
    schedule['drafts_enabled'] = False
    set_setting(db, 'automation_schedule', schedule)
    for job in db.scalars(select(Job).where(Job.kind == 'drafts', Job.status.in_(['queued', 'running']))):
        job.cancel_requested = True
        if job.status == 'queued':
            job.status = 'cancelled'
            job.result = {**(job.result or {}), 'message': 'Автоответы поставлены на паузу'}
    db.commit()
    return auto_replies_status(db)


@app.get('/ollama')
async def ollama_status(auth: AUTH):
    try:
        models = await ollama.list_models()
        return {'available': True, 'models': [m['name'] for m in models], 'selected': MODEL}
    except ValueError:
        return {'available': False, 'models': [], 'selected': MODEL, 'message': 'Локальный AI не запущен'}


sync_lock = asyncio.Lock()
job_dispatch_lock = asyncio.Lock()
maintenance_lock = asyncio.Lock()


async def run_sync_job(job_id):
    async with sync_lock, maintenance_lock:
        with Session() as db:
            job = db.get(Job, job_id)
            if not job or job.cancel_requested:
                if job:
                    job.status, job.result = 'cancelled', {'message': 'Задание отменено'}
                    db.commit()
                return
            job.status, job.progress, job.attempts = 'running', 1, job.attempts + 1
            kind = job.kind
            account_id = (job.result or {}).get('account_id') or setting(db, 'active_store', 'owner')
            db.commit()
            def update_progress(value):
                db.refresh(job)
                job.progress = value
                db.commit()
            def cancelled():
                db.refresh(job)
                return job.cancel_requested
            try:
                if kind == 'drafts':
                    since = (job.result or {}).get('since', '')
                    conditions = [
                        Review.wb_account_id == account_id,
                        Review.is_answered == False,
                        Review.status != 'publishing',
                        Draft.id.is_(None),
                    ]
                    if since:
                        conditions.append(Review.created_at >= since)
                    ids = list(db.scalars(
                        select(Review.id).outerjoin(Draft, Draft.review_id == Review.id).where(
                            *conditions,
                        ).order_by(Review.created_at.desc(), Review.id)
                    ))
                    total, processed, failed, failures = len(ids), 0, 0, []
                    for index, review_id in enumerate(ids, 1):
                        if cancelled():
                            raise asyncio.CancelledError
                        try:
                            await generate_draft(db, review_id)
                            processed += 1
                        except Exception as error:
                            db.rollback()
                            failed += 1
                            if len(failures) < 20:
                                failures.append({'review_id': review_id, 'reason': (str(error) or type(error).__name__)[:300]})
                        update_progress(round(index / max(1, total) * 100))
                    schedule = setting(db, 'automation_schedule', {})
                    proposal_id = None
                    if schedule.get('auto_propose'):
                        minimum = max(70, min(100, int(schedule.get('min_quality', 96))))
                        limit = max(1, min(100, int(schedule.get('max_proposals', 100))))
                        candidates = list(db.scalars(
                            select(Review.id).join(Draft, Draft.review_id == Review.id).where(
                                Review.wb_account_id == account_id,
                                Review.is_answered == False,
                                Review.status == 'draft_ready',
                                Review.rating >= 4,
                                Review.manual == False,
                                func.json_extract(Draft.quality, '$.score') >= minimum,
                            ).order_by(Review.created_at.desc()).limit(limit)
                        ))
                        if candidates:
                            proposal_id = propose_publish(db, candidates)['id']
                    job = db.get(Job, job_id)
                    job.status, job.progress = 'completed', 100
                    job.result = {
                        'processed': processed,
                        'failed': failed,
                        'total': total,
                        'account_id': account_id,
                        'since': since,
                        'failures': failures,
                        'message': f'Подготовлено черновиков: {processed}. Требуют ручной проверки: {failed}.',
                        'proposal_id': proposal_id,
                    }
                else:
                    operation = wb.sync_products if kind == 'products' else wb.sync_reviews
                    try:
                        count = await operation(db, account_id, update_progress, cancelled)
                    except TypeError as exc:
                        # Keep simple test/custom providers that implement the original (db) contract usable.
                        if 'positional' not in str(exc):
                            raise
                        count = await operation(db)
                    job.status, job.progress, job.result = 'completed', 100, {'processed':count, 'account_id': account_id}
                    schedule = setting(db, 'automation_schedule', {})
                    if kind == 'reviews' and schedule.get('enabled') and schedule.get('drafts_enabled') and not db.scalar(select(Job).where(Job.kind == 'drafts', Job.status.in_(['queued', 'running']))):
                        db.add(Job(id=str(uuid4()), kind='drafts', priority=250, result={'account_id': account_id, 'since': schedule.get('drafts_since', ''), 'scheduled': True, 'message': 'Подготовка ответов после получения новых отзывов'}))
                        schedule['last_drafts'] = time.time()
                        set_setting(db, 'automation_schedule', schedule)
            except asyncio.CancelledError:
                db.rollback()
                job = db.get(Job, job_id)
                job.status, job.result = 'cancelled', {**(job.result or {}), 'message': 'Задание отменено', 'account_id': account_id}
            except Exception as error:
                db.rollback()
                job = db.get(Job, job_id)
                if job.attempts < job.max_attempts and not job.cancel_requested:
                    job.status, job.result = 'queued', {**(job.result or {}), 'message': 'Повтор после ошибки', 'account_id': account_id}
                else:
                    message = 'Автоответы не завершены. Проверьте локальную модель и повторите задание.' if kind == 'drafts' else 'Синхронизация не завершена. Проверьте токен, доступ WB и журнал соединений.'
                    job.status, job.result = 'failed', {'message':message, 'account_id': account_id, 'error_type': type(error).__name__}
            db.commit()


async def run_job_queue():
    async with job_dispatch_lock:
        while True:
            with Session() as db:
                job = db.scalar(select(Job).where(Job.status == 'queued').order_by(Job.priority.asc(), Job.created_at.asc()))
                job_id = job.id if job else None
            if not job_id:
                return
            await run_sync_job(job_id)
            with Session() as db:
                retry = db.get(Job, job_id)
                if retry and retry.status == 'queued':
                    await asyncio.sleep(2)


async def scheduler_loop():
    while True:
        await asyncio.sleep(30)
        try:
            with Session() as db:
                schedule = setting(db, 'automation_schedule', {'enabled': False})
                if not schedule.get('enabled'):
                    continue
                current = time.time()
                active = setting(db, 'active_store', 'owner')
                for kind, hours in (('products', int(schedule.get('products_hours', 24))), ('reviews', int(schedule.get('reviews_hours', 3)))):
                    last = float(schedule.get('last_' + kind, 0))
                    if current - last >= max(1, hours) * 3600 and not db.scalar(select(Job).where(Job.kind == kind, Job.status.in_(['queued', 'running']))):
                        db.add(Job(id=str(uuid4()), kind=kind, priority=200, result={'account_id': active, 'scheduled': True}))
                        schedule['last_' + kind] = current
                if schedule.get('drafts_enabled'):
                    last = float(schedule.get('last_drafts', 0))
                    hours = max(1, int(schedule.get('drafts_hours', 6)))
                    if current - last >= hours * 3600 and not db.scalar(select(Job).where(Job.kind == 'drafts', Job.status.in_(['queued', 'running']))):
                        db.add(Job(id=str(uuid4()), kind='drafts', priority=250, result={'account_id': active, 'since': schedule.get('drafts_since', ''), 'scheduled': True, 'message': 'Плановая подготовка автоответов'}))
                        schedule['last_drafts'] = current
                if current - float(schedule.get('last_backup', 0)) >= max(1, int(schedule.get('backup_hours', 24))) * 3600:
                    async with maintenance_lock:
                        await asyncio.to_thread(backup)
                    schedule['last_backup'] = current
                set_setting(db, 'scheduler_status', {'state': 'ok', 'checked_at': datetime.now(timezone.utc).isoformat(), 'last_error': ''})
                set_setting(db, 'automation_schedule', schedule)
                db.commit()
            asyncio.create_task(run_job_queue())
        except Exception as error:
            # Keep the scheduler alive and expose a safe diagnostic in the interface.
            try:
                with Session() as db:
                    set_setting(db, 'scheduler_status', {'state': 'error', 'checked_at': datetime.now(timezone.utc).isoformat(), 'last_error': type(error).__name__})
                    db.commit()
            except Exception:
                pass


@app.post('/jobs/sync/{kind}')
async def schedule_sync(kind: str, background: BackgroundTasks, auth: AUTH, db: DB):
    if kind not in ('products','reviews'):
        raise HTTPException(404)
    wb_token(db)
    active = setting(db, 'active_store', 'owner')
    job = Job(id=str(uuid4()), kind=kind, result={'account_id': active})
    db.add(job)
    db.commit()
    background.add_task(run_job_queue)
    return public(job)


@app.post('/jobs/drafts')
async def schedule_all_drafts(background: BackgroundTasks, auth: AUTH, db: DB):
    active = setting(db, 'active_store', 'owner')
    existing = db.scalar(select(Job).where(Job.kind == 'drafts', Job.status.in_(['queued', 'running'])))
    if existing:
        return public(existing)
    job = Job(id=str(uuid4()), kind='drafts', priority=50, result={'account_id': active, 'message': 'Подготовка ответов поставлена в очередь'})
    db.add(job)
    db.commit()
    background.add_task(run_job_queue)
    return public(job)


@app.get('/jobs/drafts/preview')
def preview_all_drafts(auth: AUTH, db: DB):
    active = setting(db, 'active_store', 'owner')
    base = select(func.count()).select_from(Review).outerjoin(Draft, Draft.review_id == Review.id).where(
        Review.wb_account_id == active,
        Review.is_answered == False,
        Review.status != 'publishing',
        Draft.id.is_(None),
    )
    total = db.scalar(base) or 0
    quick = db.scalar(base.where(Review.text == '', Review.rating >= 4)) or 0
    ai = max(0, total - quick)
    estimated_seconds = quick * 0.05 + ai * 25
    return {'total': total, 'quick': quick, 'ai': ai, 'estimated_minutes': max(1, round(estimated_seconds / 60)) if total else 0}


@app.get('/jobs')
def jobs(auth: AUTH, db: DB):
    return [public(j) for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(100))]


@app.post('/jobs/{jid}/cancel')
def cancel_job(jid: str, auth: AUTH, db: DB):
    job = db.get(Job, jid)
    if not job or job.status not in ('queued', 'running'):
        raise ValueError('Это задание уже завершено')
    job.cancel_requested = True
    if job.status == 'queued':
        job.status = 'cancelled'
    db.commit()
    return public(job)


@app.post('/jobs/{jid}/retry')
async def retry_job(jid: str, background: BackgroundTasks, auth: AUTH, db: DB):
    job = db.get(Job, jid)
    if not job or job.status not in ('failed', 'interrupted', 'cancelled'):
        raise ValueError('Повтор доступен только для незавершённого задания')
    job.status, job.cancel_requested, job.progress, job.attempts = 'queued', False, 0, 0
    db.commit()
    background.add_task(run_job_queue)
    return public(job)


@app.get('/jobs/{jid}')
def get_job(jid: str, auth: AUTH, db: DB):
    job = db.get(Job,jid)
    if not job:
        raise HTTPException(404)
    return public(job)


@app.post('/sync/{kind}')
async def sync(kind: str, auth: AUTH, db: DB):
    if sync_lock.locked():
        raise ValueError('Синхронизация уже выполняется')
    async with sync_lock:
        if kind == 'products':
            count = await wb.sync_products(db)
        elif kind == 'reviews':
            count = await wb.sync_reviews(db)
        else:
            raise HTTPException(404)
    return {'processed': count}


@app.get('/reviews')
def reviews(auth: AUTH, db: DB, q: str = '', unanswered: bool = False, max_rating: int = 5, days: int = 0, product_id: str = '', limit: int = 100, attention: bool = False):
    return reviews_search(db, q, unanswered, max_rating, days, product_id, limit, account_id=setting(db, 'active_store', 'owner'), attention=attention)


@app.get('/reviews/page')
def reviews_paginated(auth: AUTH, db: DB, q: str = '', unanswered: bool = False, max_rating: int = 5, days: int = 0, product_id: str = '', limit: int = 30, offset: int = 0, attention: bool = False):
    return reviews_page(db, q=q, unanswered=unanswered, max_rating=max_rating, days=days, product_id=product_id, limit=limit, offset=offset, account_id=setting(db, 'active_store', 'owner'), attention=attention)


@app.get('/products')
def products(auth: AUTH, db: DB, q: str = ''):
    return [public(p) for p in db.scalars(select(Product).where(Product.name.contains(q, autoescape=True) | Product.brand.contains(q, autoescape=True)).limit(200))]


@app.get('/products/page')
def products_paginated(auth: AUTH, db: DB, q: str = '', limit: int = 60, offset: int = 0):
    limit, offset = min(200, max(1, limit)), max(0, offset)
    query = select(Product)
    count_query = select(func.count()).select_from(Product)
    if q:
        condition = Product.name.contains(q, autoescape=True) | Product.brand.contains(q, autoescape=True) | Product.category.contains(q, autoescape=True)
        query, count_query = query.where(condition), count_query.where(condition)
    items = [public(p) for p in db.scalars(query.order_by(Product.name, Product.id).offset(offset).limit(limit))]
    total = db.scalar(count_query) or 0
    return {'items': items, 'total': total, 'limit': limit, 'offset': offset, 'has_more': offset + len(items) < total}


class Fact(Payload):
    text: str = Field(min_length=2, max_length=2000)
    source: str = Field(min_length=2, max_length=1000)
    kind: str = Field(default='instruction', pattern='^(instruction|restriction|faq|feature)$')


@app.post('/products/{pid}/facts')
def product_fact(pid: str, body: Fact, auth: AUTH, db: DB):
    product = db.get(Product, pid)
    if not product:
        raise HTTPException(404)
    product.facts = product.facts + [{**body.model_dump(), 'verified_at': now(), 'verification_status': 'VERIFIED'}]
    db.commit()
    return public(product)


@app.get('/analytics')
def stats(auth: AUTH, db: DB, days: int = 0, product_id: str = ''):
    return analytics(db, days, product_id, setting(db, 'active_store', 'owner'))


@app.get('/quality-report')
def reply_quality(auth: AUTH, db: DB, days: int = 30):
    return quality_report(db, days, setting(db, 'active_store', 'owner'))


class DraftInput(Payload):
    instruction: str = Field(default='', max_length=1000)


@app.post('/reviews/{rid}/draft')
async def draft(rid: str, body: DraftInput, auth: AUTH, db: DB):
    return await generate_draft(db, rid, body.instruction)


class DraftEdit(Payload):
    text: str = Field(min_length=2, max_length=5000)
    revision: int


@app.patch('/drafts/{did}')
def draft_edit(did: str, body: DraftEdit, auth: AUTH, db: DB):
    return edit_draft(db, did, body.text, body.revision)


class DraftFeedback(Payload):
    rating: str = Field(pattern='^(positive|negative)$')
    reasons: list[str] = Field(default_factory=list, max_length=5)


@app.post('/drafts/{did}/feedback')
def draft_feedback(did: str, body: DraftFeedback, auth: AUTH, db: DB):
    draft = db.get(Draft, did)
    if not draft:
        raise HTTPException(404, 'Черновик не найден')
    reasons = [reason.strip()[:100] for reason in body.reasons if reason.strip()][:5]
    quality = dict(draft.quality or {})
    quality['owner_feedback'] = {'rating': body.rating, 'reasons': reasons, 'at': now()}
    draft.quality = quality
    review = db.get(Review, draft.review_id)
    key = 'reply_feedback:' + review.wb_account_id
    summary = setting(db, key, {'positive': 0, 'negative': 0, 'reasons': {}})
    summary[body.rating] = int(summary.get(body.rating, 0)) + 1
    for reason in reasons:
        summary.setdefault('reasons', {})[reason] = int(summary['reasons'].get(reason, 0)) + 1
    set_setting(db, key, summary)
    db.commit()
    return {'ok': True, 'feedback': quality['owner_feedback']}


class PublishInput(Payload):
    review_ids: list[str] = Field(min_length=1, max_length=100)


@app.post('/actions/publish')
def publish_proposal(body: PublishInput, auth: AUTH, db: DB):
    return propose_publish(db, body.review_ids)


@app.get('/actions')
def actions(auth: AUTH, db: DB):
    active_statuses = ['pending', 'executing', 'needs_review']
    return [public(a) for a in db.scalars(select(Action).where(Action.status.in_(active_statuses)).order_by(Action.created_at.desc()).limit(100))]


@app.post('/publications/reconcile')
async def reconcile_publications(auth: AUTH, db: DB):
    active = setting(db, 'active_store', 'owner')
    wb_token(db, active)
    await wb.sync_reviews(db, active)
    checked, confirmed, mismatched = 0, 0, 0
    for publication in db.scalars(select(Publication).where(Publication.status.in_(['uncertain', 'in_flight']))):
        review = db.get(Review, publication.review_id)
        draft = db.scalar(select(Draft).where(Draft.review_id == review.id))
        checked += 1
        if review.is_answered and draft and review.existing_answer.strip() == draft.text.strip():
            publication.status, review.status = 'published', 'published'
            confirmed += 1
        elif review.is_answered:
            publication.status, review.status = 'mismatch', 'manual_review'
            mismatched += 1
    db.commit()
    return {'checked': checked, 'confirmed': confirmed, 'mismatched': mismatched}


@app.delete('/actions/{aid}')
def remove_action(aid: str, auth: AUTH, db: DB):
    action = db.get(Action, aid)
    if not action:
        raise HTTPException(404, 'Запись действия не найдена')
    if action.kind != 'memory' or action.status in ('pending', 'executing'):
        raise ValueError('Можно удалить только завершённую или отменённую запись о правиле')
    db.delete(action)
    db.commit()
    return {'ok': True, 'id': aid}


class Confirm(Payload):
    manual_ack: bool = False


action_lock = asyncio.Lock()


@app.post('/actions/{aid}/confirm')
async def confirmation(aid: str, body: Confirm, auth: AUTH, db: DB):
    async with action_lock:
        return await confirm_action(db, aid, body.manual_ack)


@app.post('/actions/{aid}/cancel')
def cancel(aid: str, auth: AUTH, db: DB):
    action = db.get(Action, aid)
    if not action or action.status != 'pending':
        raise ValueError('Действие уже обработано')
    action.status = 'cancelled'
    if action.kind == 'publish':
        for item in action.payload['items']:
            review = db.get(Review, item['review_id'])
            if review.status == 'pending_confirmation':
                review.status = 'manual_review' if review.manual else 'draft_ready'
    db.commit()
    return {'ok': True}


class MemoryInput(Payload):
    text: str = Field(min_length=2, max_length=2000)
    kind: str = 'GLOBAL_RULE'
    scope: str = 'global'


@app.post('/memory/propose')
def memory_propose(body: MemoryInput, auth: AUTH, db: DB):
    action = Action(id=str(uuid4()), kind='memory', payload=body.model_dump())
    db.add(action)
    db.commit()
    return public(action)


@app.patch('/actions/{aid}/memory')
def memory_scope(aid: str, body: MemoryInput, auth: AUTH, db: DB):
    action = db.get(Action, aid)
    if not action or action.status != 'pending' or action.kind != 'memory':
        raise ValueError('Нет ожидающего предложения памяти')
    action.payload = body.model_dump()
    db.commit()
    return public(action)


@app.get('/memory')
def memories(auth: AUTH, db: DB):
    return [public(m) for m in db.scalars(select(Memory))]


@app.delete('/memory/{mid}')
def memory_delete(mid: str, auth: AUTH, db: DB):
    result = db.execute(delete(Memory).where(Memory.id == mid))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(404, 'Правило уже удалено или не найдено')
    db.commit()
    return {'ok': True, 'id': mid}


class Chat(Payload):
    text: str = Field(min_length=1, max_length=5000)
    conversation_id: str | None = None
    selection: dict[str,str] = Field(default_factory=dict)


chat_lock = asyncio.Lock()


@app.post('/chat')
async def chat(body: Chat, auth: AUTH, db: DB):
    async with chat_lock:
        return await assistant.send(db, body.text, body.conversation_id, body.selection)


class VoiceText(Payload):
    text: str = Field(min_length=1, max_length=3000)
    speaker: str = Field(default='aidar', max_length=20)


@app.get('/voice/status')
def voice_status(auth: AUTH):
    return voice.status()


@app.post('/voice/tts')
async def voice_tts(body: VoiceText, auth: AUTH):
    audio = await asyncio.to_thread(voice.synthesize, body.text, body.speaker)
    return Response(content=audio, media_type='audio/wav', headers={'Content-Disposition':'inline; filename="assistant.wav"'})


@app.post('/voice/stt')
async def voice_stt(auth: AUTH, audio: UploadFile = File(...)):
    if audio.content_type not in ('audio/wav', 'audio/x-wav', 'application/octet-stream'):
        raise ValueError('Поддерживается запись WAV')
    raw = await audio.read(12 * 1024 * 1024 + 1)
    text_value = await asyncio.to_thread(voice.transcribe, raw)
    return {'text': text_value}


@app.get('/conversations')
def conversations(auth: AUTH, db: DB):
    return [public(c) for c in db.scalars(select(Conversation))]


@app.get('/conversations/{cid}')
def history(cid: str, auth: AUTH, db: DB):
    return [public(m) for m in db.scalars(select(Message).where(Message.conversation_id == cid).order_by(Message.created_at))]


class Title(Payload):
    title: str = Field(min_length=1, max_length=100)


@app.patch('/conversations/{cid}')
def rename(cid: str, body: Title, auth: AUTH, db: DB):
    conversation = db.get(Conversation, cid)
    if not conversation:
        raise HTTPException(404)
    conversation.title = body.title
    db.commit()
    return {'ok': True}


@app.delete('/conversations/{cid}')
def remove_conversation(cid: str, auth: AUTH, db: DB):
    db.execute(delete(Conversation).where(Conversation.id == cid))
    db.commit()
    return {'ok': True}


@app.post('/catalog/import')
async def catalog_import(auth: AUTH, db: DB, file: UploadFile = File(...), source: str = Form(...), verified: bool = Form(False)):
    raw = await file.read(10 * 1024 * 1024 + 1)
    try:
        return import_catalog(db, raw, file.filename or '', source, verified)
    except (KeyError, TypeError, UnicodeError, StopIteration):
        raise ValueError('Неверный формат каталога') from None


@app.get('/catalog')
def catalog(auth: AUTH, db: DB):
    return {'sources': [public(s) for s in db.scalars(select(CatalogSource))], 'imports': [public(s) for s in db.scalars(select(CatalogImport))], 'parts': [public(s) for s in db.scalars(select(CatalogPart).limit(200))]}


class VehicleInput(Payload):
    vin: str = ''
    profile: dict = Field(default_factory=dict)


@app.post('/vin')
def vin(body: VehicleInput, auth: AUTH, db: DB):
    value = validate_vin(body.vin)
    # Salted fingerprint is local; full VIN is not persisted.
    salt = setting(db, 'vin_salt')
    if not salt:
        salt = secrets.token_hex(32)
        set_setting(db, 'vin_salt', salt)
        db.commit()
    hashed = digest(salt + value)
    known = db.get(VinSession, hashed)
    if body.profile:
        if not body.profile.get('source') or not body.profile.get('verified'):
            raise ValueError('Нужен источник и ручное подтверждение профиля')
        profile = {k: v for k, v in body.profile.items() if k not in ('vin', 'VIN')}
        vehicle = Vehicle(id=known.vehicle_id if known else str(uuid4()), profile=profile)
        db.merge(vehicle)
        db.flush()
        db.merge(VinSession(vin_hash=hashed, vin_mask=mask_vin(value), vehicle_id=vehicle.id))
        db.commit()
        return {'status': 'MANUAL_VERIFIED', 'vehicle_id': vehicle.id, 'profile': profile, 'vin_mask': mask_vin(value)}
    if known:
        return {'status': 'LOCAL_MATCH', 'profile': db.get(Vehicle, known.vehicle_id).profile, 'vin_mask': known.vin_mask}
    return {'status': 'INSUFFICIENT_DATA', 'vin_mask': mask_vin(value), 'message': 'VIN корректен по формату. В локальной базе нет расшифровки. Введите профиль из проверенного источника.'}


class Fitment(Payload):
    vehicle: dict
    part_number: str = ''
    oe: str = ''
    same_brand: str = ''
    category: str = ''


@app.post('/compatibility')
def fitment(body: Fitment, auth: AUTH, db: DB):
    return check_compatibility(db, **body.model_dump())


class BrandInput(Payload):
    brand: str = Field(min_length=1, max_length=100)
    category: str = ''
    priority: int = 100
    enabled: bool = True


@app.get('/brands')
def brands(auth: AUTH, db: DB):
    return [public(b) for b in db.scalars(select(BrandPreference))]


@app.post('/brands')
def brand_save(body: BrandInput, auth: AUTH, db: DB):
    db.merge(BrandPreference(**body.model_dump()))
    db.commit()
    return {'ok': True}


@app.post('/backup')
def backup_create(auth: AUTH):
    return {'file': backup(), 'secrets_included': False}


class PortablePassword(Payload):
    password: str = Field(min_length=12, max_length=1024)


portable_lock = asyncio.Lock()


@app.post('/portable/export')
async def portable_export(body: PortablePassword, auth: AUTH):
    path = await asyncio.to_thread(portable_backup, body.password)
    return FileResponse(path, media_type='application/octet-stream', filename=path.name, headers={'X-Backup-Encrypted':'AES-256-GCM','X-Secrets-Included':'false'})


@app.post('/portable/restore')
async def portable_import(auth: AUTH, password: str = Form(...), file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith('.wbai'):
        raise ValueError('Выберите зашифрованную копию .wbai')
    if len(password) < 12:
        raise ValueError('Пароль копии должен содержать не менее 12 символов')
    upload = DATA / 'temp' / f'upload-{uuid4().hex}.wbai'
    size = 0
    try:
        with upload.open('wb') as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 1024 * 1024 * 1024:
                    raise ValueError('Резервная копия слишком большая')
                output.write(chunk)
        with Session() as db:
            active_jobs = db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(['queued', 'running']))) or 0
        if active_jobs:
            raise ValueError('Сначала дождитесь завершения или отмените задания в очереди')
        async with portable_lock, maintenance_lock:
            app.state.maintenance = True
            try:
                return await asyncio.to_thread(portable_restore, upload, password)
            finally:
                app.state.maintenance = False
    finally:
        upload.unlink(missing_ok=True)


@app.get('/export/reviews')
def export_reviews(auth: AUTH, db: DB):
    # Explicit allowlist; never export ORM internals, accounts, settings or sessions.
    return [{'id': r.id, 'rating': r.rating, 'text': mask_vin(r.text), 'status': r.status} for r in db.scalars(select(Review))]
