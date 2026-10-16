import json
import os
import re
import zipfile
import csv
import secrets
import time
import hashlib
import asyncio
import shutil
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from typing import Annotated, Literal
from fastapi import FastAPI, Depends, Request, Response, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, delete, text, func, case
from sqlalchemy.exc import IntegrityError
from .db import *
from .config import ORIGIN, ORIGINS, DATA, DATABASE, DATABASE_URL, MODEL, REAL_PUBLISH, SQLITE_STARTUP_HEALTH
from .security import digest, encrypt_secret, decrypt_secret, guard
from .integrations import ollama, wb, ozon, yandex, wb_token, provider_for, web_research
from .services import reviews_search, reviews_page, review_filter_counts, analytics, quality_report, generate_draft, edit_draft, propose_publish, confirm_action, question_intent, remember_web_instruction_sources, draft_public, review_items
from .assistant import assistant
from .compatibility import import_catalog, validate_vin, check_compatibility
from .safety import mask_vin
from .maintenance import backup, portable_backup, portable_restore, portable_verify, database_health, recovery_snapshot
from . import voice
from .activity_history import activity_page, rule_details
from .generation_monitor import generation_metrics
from .reviewed_knowledge import product_scope,product_owned,knowledge_report,refresh_reviewed_knowledge
from .fitment_catalog import import_cross_catalog, catalog_summary, VIN as FITMENT_VIN, vehicle_identifiers, vin_key, oem_numbers, fitment_answer, save_question_evidence


@asynccontextmanager
async def lifespan(app):
    from alembic.config import Config
    from alembic import command
    cfg = Config(str(Path(__file__).parent.parent / 'alembic.ini'))
    cfg.set_main_option('script_location', str(Path(__file__).parent.parent / 'migrations'))
    command.upgrade(cfg, 'head')
    with Session() as db:
        db.merge(StoreProfile(id='owner', name='Мой магазин', provider='wb'))
        legacy_hash = setting(db, 'password_hash')
        if legacy_hash and not db.scalar(select(func.count()).select_from(User)):
            db.add(User(id='owner', username='owner', display_name='Владелец', password_hash=legacy_hash, role='owner', position='Владелец', permissions=['*'], store_ids=['*'], enabled=True, must_change_password=False))
        if not setting(db, 'active_store'):
            set_setting(db, 'active_store', 'owner')
        for job in db.scalars(select(Job).where(Job.status.in_(['queued','running']))):
            if job.kind in ('products', 'reviews', 'questions') and not job.cancel_requested:
                job.status = 'queued'
                job.result = {**(job.result or {}), 'message': 'Продолжение синхронизации после перезапуска'}
            else:
                job.status, job.result = 'interrupted', {**(job.result or {}), 'message':'Приложение перезапущено; повторите задание'}
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


app = FastAPI(title='Ассистент маркетплейсов', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.state.maintenance = False
def allowed_origin(origin):
    return origin in ORIGINS


app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_credentials=True, allow_methods=['GET', 'POST', 'PATCH', 'DELETE'], allow_headers=['Content-Type', 'X-CSRF-Token'])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver', 'assistant.blackmindworks.com'] + (['backend'] if os.environ.get('WB_CONTAINER') == '1' else []))


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
    actor = getattr(request.state, 'actor', None)
    if actor and request.method in ('POST', 'PATCH', 'DELETE') and response.status_code < 400:
        try:
            with Session() as audit_db:
                parts = [part for part in request.url.path.split('/') if part]
                entity_type = parts[0] if parts else ''
                entity_id = parts[1] if len(parts) > 1 and len(parts[1]) < 100 else ''
                audit_db.add(Activity(user_id=actor['user_id'], username=actor['username'], method=request.method, path=request.url.path[:300], status=response.status_code, entity_type=entity_type, entity_id=entity_id, details={'query_keys':sorted(request.query_params.keys()), **getattr(request.state,'activity_details',{})}))
                audit_db.commit()
        except Exception:
            pass
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
        user = db.get(User, session.user_id) if session else None
        if not session or session.expires < time.time() or not user or not user.enabled:
            raise HTTPException(401, 'Войдите в приложение')
        if user.must_change_password and request.url.path not in ('/auth/session', '/auth/password', '/auth/logout'):
            raise HTTPException(403, 'Сначала замените временный пароль')
        if request.method not in ('GET', 'HEAD') and not secrets.compare_digest(session.csrf, request.headers.get('x-csrf-token', '')):
            raise HTTPException(403, 'Неверный CSRF-токен')
        request.state.actor = {'user_id': user.id, 'username': user.username}
        permissions = ['*'] if user.role == 'owner' else (user.permissions or permission_preset(user.position))
        return {'csrf': session.csrf, 'user_id': user.id, 'username': user.username, 'display_name': user.display_name, 'role': user.role, 'position': user.position, 'permissions': permissions, 'store_ids': user.store_ids or [], 'must_change_password': user.must_change_password, 'can_manage_wb': '*' in permissions or 'wb:operate' in permissions}


AUTH = Annotated[dict, Depends(authorize)]


def owner(auth: AUTH):
    if auth['role'] != 'owner':
        raise HTTPException(403, 'Это действие доступно только владельцу')
    return auth


OWNER = Annotated[dict, Depends(owner)]


def wb_operator(auth: AUTH):
    if '*' not in auth.get('permissions', []) and 'wb:operate' not in auth.get('permissions', []):
        raise HTTPException(403, 'Операции Wildberries доступны владельцу и менеджеру WB')
    return auth


WB_OPERATOR = Annotated[dict, Depends(wb_operator)]


def marketplace_operator(auth: AUTH):
    if '*' not in auth.get('permissions', []) and not any(p.endswith(':operate') for p in auth.get('permissions', [])):
        raise HTTPException(403, 'Нужны права менеджера маркетплейса')
    return auth


MARKET_OPERATOR = Annotated[dict, Depends(marketplace_operator)]


def permission_preset(position):
    provider = {'Менеджер WB':'wb', 'Менеджер Ozon':'ozon', 'Менеджер Яндекс Маркет':'yandex'}.get(position)
    return ['data:read', 'assistant:use'] + ([provider + ':operate'] if provider else [])


def can(auth, permission):
    if '*' not in auth.get('permissions', []) and permission not in auth.get('permissions', []):
        raise HTTPException(403, 'Недостаточно прав для этого действия')


def can_operate_store(auth, store):
    if not store:
        raise HTTPException(404, 'Магазин не найден')
    allowed = auth.get('store_ids') or []
    if auth['role'] != 'owner' and '*' not in allowed and store.id not in allowed:
        raise HTTPException(403, 'Вам доступен просмотр магазина, но операции не назначены владельцем')
    can(auth, store.provider + ':operate')


def active_store(db, auth):
    key = 'active_store:' + auth['user_id']
    store_id = setting(db, key, setting(db, 'active_store', 'owner'))
    if not db.get(StoreProfile, store_id):
        store_id = db.scalar(select(StoreProfile.id).order_by(StoreProfile.created_at).limit(1)) or ''
    return store_id


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Password(Payload):
    username: str = Field(default='owner', min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class SetupPassword(Payload):
    username: str = Field(default='owner', min_length=3, max_length=64)
    display_name: str = Field(default='Владелец', min_length=2, max_length=100)
    password: str = Field(min_length=12, max_length=1024)
    confirmation: str = Field(min_length=12, max_length=1024)


login_attempts = {}
login_lock = asyncio.Lock()
setup_lock = asyncio.Lock()
register_lock = asyncio.Lock()
SESSION_SECONDS = 30 * 24 * 3600


def normalized_username(value):
    username = value.strip().lower()
    if not username.replace('.', '').replace('_', '').replace('-', '').isalnum():
        raise ValueError('Логин может содержать буквы, цифры, точку, дефис и подчёркивание')
    return username


def session_response(user, csrf):
    permissions = ['*'] if user.role == 'owner' else (user.permissions or permission_preset(user.position))
    return {'csrf': csrf, 'user': {'id': user.id, 'username': user.username, 'display_name': user.display_name, 'role': user.role, 'position': user.position, 'permissions': permissions, 'store_ids': user.store_ids or [], 'must_change_password': user.must_change_password, 'can_manage_wb': '*' in permissions or 'wb:operate' in permissions}}


def set_session_cookie(response, request, token):
    forwarded = request.headers.get('x-forwarded-proto', '')
    secure = forwarded == 'https' or request.headers.get('origin', '').startswith('https://')
    response.set_cookie('wb_session', token, httponly=True, secure=secure, samesite='strict', max_age=SESSION_SECONDS, path='/')


@app.get('/health')
def health(db: DB):
    db.execute(text('SELECT 1'))
    return {'status': 'ok', 'database': 'ok'}


@app.post('/auth/login')
async def login(body: Password, response: Response, request: Request, db: DB):
    async with login_lock:
        current = time.time()
        username = normalized_username(body.username)
        attempts = [t for t in login_attempts.get(username, []) if current - t < 300]
        login_attempts[username] = attempts
        if len(attempts) >= 5:
            raise HTTPException(429, 'Слишком много попыток. Повторите через 5 минут')
        user = db.scalar(select(User).where(User.username == username))
        if not user:
            pending = db.scalar(select(RegistrationRequest).where(RegistrationRequest.username == username, RegistrationRequest.status == 'pending'))
            if pending:
                try:
                    PasswordHasher().verify(pending.password_hash, body.password)
                except (VerificationError, InvalidHashError):
                    pass
                else:
                    raise HTTPException(403, 'Ваша заявка ожидает одобрения владельцем')
            attempts.append(current)
            if not db.scalar(select(func.count()).select_from(User)):
                raise HTTPException(409, 'Сначала создайте аккаунт владельца')
            raise HTTPException(401, 'Неверный логин или пароль')
        if not user.enabled:
            raise HTTPException(403, 'Учётная запись отключена владельцем')
        try:
            PasswordHasher().verify(user.password_hash, body.password)
        except (VerificationError, InvalidHashError):
            attempts.append(current)
            raise HTTPException(401, 'Неверный пароль') from None
        login_attempts.pop(username, None)
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        db.execute(delete(LoginSession).where(LoginSession.expires < current))
        db.add(LoginSession(id=digest(token), expires=int(current + SESSION_SECONDS), csrf=csrf, user_id=user.id))
        db.commit()
        set_session_cookie(response, request, token)
        return session_response(user, csrf)


@app.get('/auth/status')
def auth_status(db: DB):
    return {'initialized': bool(db.scalar(select(func.count()).select_from(User)) or setting(db, 'password_hash'))}


class InviteInput(Payload):
    position: Literal['Менеджер WB', 'Менеджер Ozon', 'Менеджер Яндекс Маркет'] = 'Менеджер WB'
    store_ids: list[str] = Field(default_factory=lambda: ['*'], max_length=100)
    days: int = Field(default=7, ge=1, le=30)


class RegisterInput(Payload):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=1024)


class RegistrationReceipt(Payload):
    receipt: str = Field(min_length=32, max_length=128)


class RegistrationDecision(Payload):
    decision: Literal['accept', 'reject']
    position: Literal['Менеджер WB', 'Менеджер Ozon', 'Менеджер Яндекс Маркет'] = 'Менеджер WB'


REGISTRATION_APPROVER = 'ismailov.kamil'
registration_attempts = {}


def registration_owner(auth):
    if auth['role'] != 'owner' or auth['username'] != REGISTRATION_APPROVER:
        raise HTTPException(403, 'Заявками управляет ismailov.kamil')


@app.post('/auth/invites')
def create_invite(body: InviteInput, auth: OWNER, db: DB):
    if '*' in body.store_ids:
        store_ids = ['*']
    else:
        available = set(db.scalars(select(StoreProfile.id)))
        store_ids = list(dict.fromkeys(body.store_ids))
        if not store_ids or not set(store_ids) <= available:
            raise ValueError('Выберите доступные магазины')
    token = secrets.token_urlsafe(32)
    invitation = TeamInvite(token_hash=digest(token), position=body.position, store_ids=store_ids,
                            created_by=auth['user_id'], expires=int(time.time()) + body.days * 86400)
    db.add(invitation)
    db.commit()
    return {'invite': token, 'position': invitation.position, 'expires': invitation.expires,
            'store_ids': store_ids}


@app.get('/auth/invites/{token}')
def invite_details(token: str, db: DB):
    invitation = db.get(TeamInvite, digest(token)) if 32 <= len(token) <= 128 else None
    if not invitation or invitation.used_by or invitation.expires < time.time():
        raise HTTPException(404, 'Приглашение недействительно или уже использовано')
    return {'position': invitation.position, 'expires': invitation.expires}


@app.post('/auth/register')
async def register_colleague(body: RegisterInput, request: Request, db: DB):
    username = normalized_username(body.username)
    if len(username) < 3:
        raise ValueError('Логин должен содержать не менее 3 символов')
    async with register_lock:
        current = time.time()
        key = request.client.host if request.client else 'unknown'
        for old_key in list(registration_attempts):
            registration_attempts[old_key] = [t for t in registration_attempts[old_key] if current - t < 600]
            if not registration_attempts[old_key]:
                del registration_attempts[old_key]
        attempts = registration_attempts.setdefault(key, [])
        if len(attempts) >= 20 or len(registration_attempts) > 5000:
            raise HTTPException(429, 'Слишком много заявок. Повторите через 10 минут')
        attempts.append(current)
        approver = db.scalar(select(User).where(User.username == REGISTRATION_APPROVER, User.enabled == True, User.role == 'owner'))
        if not approver:
            raise HTTPException(503, 'Регистрация временно недоступна. Обратитесь к владельцу')
        if db.scalar(select(User.id).where(User.username == username)) or db.scalar(select(RegistrationRequest.id).where(RegistrationRequest.username == username)):
            raise HTTPException(409, 'Этот логин уже занят или заявка уже отправлена. Попробуйте войти либо выберите другой логин')
        receipt = secrets.token_urlsafe(32)
        item = RegistrationRequest(id=str(uuid4()), username=username, password_hash=PasswordHasher().hash(body.password), receipt_hash=digest(receipt), status='pending')
        db.add(item)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Заявка с таким логином уже отправлена') from None
    return {'status': 'pending', 'username': username, 'receipt': receipt}


@app.post('/auth/registration/status')
def registration_status(body: RegistrationReceipt, db: DB):
    item = db.scalar(select(RegistrationRequest).where(RegistrationRequest.receipt_hash == digest(body.receipt)))
    if not item:
        raise HTTPException(404, 'Заявка не найдена')
    return {'status': item.status, 'username': item.username}


@app.get('/registration-requests')
def registration_requests(auth: AUTH, db: DB):
    registration_owner(auth)
    return [{'id': item.id, 'username': item.username, 'created_at': item.created_at}
            for item in db.scalars(select(RegistrationRequest).where(RegistrationRequest.status == 'pending').order_by(RegistrationRequest.created_at))]


@app.post('/registration-requests/{request_id}/decision')
async def registration_decide(request_id: str, body: RegistrationDecision, request: Request, auth: AUTH, db: DB):
    registration_owner(auth)
    async with register_lock:
        db.rollback()
        db.execute(text('BEGIN IMMEDIATE' if IS_SQLITE else 'BEGIN'))
        item = db.scalar(select(RegistrationRequest).where(RegistrationRequest.id == request_id).with_for_update())
        if not item:
            raise HTTPException(404, 'Заявка не найдена')
        if item.status != 'pending':
            raise HTTPException(409, 'Эта заявка уже рассмотрена. Обновите список')
        if body.decision == 'accept':
            if db.scalar(select(User.id).where(User.username == item.username)):
                raise HTTPException(409, 'Такой пользователь уже существует')
            stores = list(db.scalars(select(StoreProfile.id).where(StoreProfile.provider == provider_for_position(body.position))))
            user = User(id=str(uuid4()), username=item.username, display_name=item.username, password_hash=item.password_hash,
                        role='member', position=body.position, permissions=permission_preset(body.position),
                        store_ids=stores, enabled=True, must_change_password=False)
            db.add(user)
            item.status = 'accepted'
        else:
            item.status = 'rejected'
        item.password_hash = ''
        item.reviewed_at, item.reviewed_by = now(), auth['user_id']
        request.state.activity_details = {'event': 'registration_' + item.status, 'target_username': item.username}
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Пользователь уже создан. Обновите список') from None
    return {'status': item.status}


def provider_for_position(position):
    return {'Менеджер WB': 'wb', 'Менеджер Ozon': 'ozon', 'Менеджер Яндекс Маркет': 'yandex'}[position]


@app.post('/auth/setup')
async def auth_setup(body: SetupPassword, response: Response, request: Request, db: DB):
    if body.password != body.confirmation:
        raise ValueError('Пароли не совпадают')
    async with setup_lock:
        db.rollback()
        db.execute(text('BEGIN IMMEDIATE' if IS_SQLITE else 'BEGIN'))
        if db.scalar(select(func.count()).select_from(User)) or setting(db, 'password_hash'):
            raise HTTPException(409, 'Пароль владельца уже создан')
        username = normalized_username(body.username)
        password_hash = PasswordHasher().hash(body.password)
        set_setting(db, 'password_hash', password_hash)
        user = User(id='owner', username=username, display_name=body.display_name.strip(), password_hash=password_hash, role='owner', position='Владелец', permissions=['*'], store_ids=['*'], enabled=True, must_change_password=False)
        db.add(user)
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        db.add(LoginSession(id=digest(token), expires=int(time.time() + SESSION_SECONDS), csrf=csrf, user_id='owner'))
        db.commit()
        set_session_cookie(response, request, token)
        return session_response(user, csrf)


@app.get('/auth/session')
def session(auth: AUTH):
    return {'csrf': auth['csrf'], 'user': {k: auth[k] for k in ('user_id','username','display_name','role','position','permissions','store_ids','must_change_password','can_manage_wb')} | {'id':auth['user_id']}}


@app.post('/auth/logout')
def logout(request: Request, response: Response, auth: AUTH, db: DB):
    db.execute(delete(LoginSession).where(LoginSession.id == digest(request.cookies['wb_session'])))
    db.commit()
    response.delete_cookie('wb_session')
    return {'ok': True}


class TeamUserInput(Payload):
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=12, max_length=1024)
    position: Literal['Менеджер WB', 'Менеджер Ozon', 'Менеджер Яндекс Маркет'] = 'Менеджер WB'


class TeamUserState(Payload):
    enabled: bool | None = None
    display_name: str | None = Field(default=None, min_length=2, max_length=100)
    position: Literal['Менеджер WB', 'Менеджер Ozon', 'Менеджер Яндекс Маркет'] | None = None
    permissions: list[str] | None = None
    store_ids: list[str] | None = None


class TeamPassword(Payload):
    password: str = Field(min_length=12, max_length=1024)


class OwnPassword(Payload):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


def public_user(user):
    return {k: getattr(user, k) for k in ('id', 'username', 'display_name', 'role', 'position', 'permissions', 'store_ids', 'enabled', 'must_change_password', 'created_at')}


@app.get('/users')
def users_list(auth: OWNER, db: DB):
    return [public_user(user) for user in db.scalars(select(User).order_by(User.role.desc(), User.username)).all()]


@app.get('/team/activity')
def team_activity(auth: OWNER, db: DB):
    return [public(item) for item in db.scalars(select(Activity).order_by(Activity.id.desc()).limit(200)).all()]


@app.get('/activity/page')
def user_activity(auth: AUTH, db: DB, q: str = '', user_id: str = '', before: int = 0, limit: int = 50):
    return activity_page(db,auth,q,user_id,before,limit)


@app.get('/team/dashboard')
def team_dashboard(auth: OWNER, db: DB, days: int = 30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1,min(days,365)))).isoformat()
    users = list(db.scalars(select(User).order_by(User.display_name)))
    result = []
    for user in users:
        actions = db.scalar(select(func.count()).select_from(Activity).where(Activity.user_id == user.id, Activity.timestamp >= cutoff)) or 0
        drafts = db.scalar(select(func.count()).select_from(Draft).where(Draft.updated_by == user.id)) or 0
        result.append({'user':public_user(user), 'actions':actions, 'drafts':drafts})
    return {'period_days':days, 'members':result, 'totals':{'users':len(users),'actions':sum(x['actions'] for x in result),'drafts':sum(x['drafts'] for x in result)}}


@app.post('/users')
def users_create(body: TeamUserInput, auth: OWNER, db: DB):
    username = normalized_username(body.username)
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, 'Пользователь с таким логином уже существует')
    user = User(id=str(uuid4()), username=username, display_name=body.display_name.strip(), password_hash=PasswordHasher().hash(body.password), role='member', position=body.position, permissions=permission_preset(body.position), store_ids=[], enabled=True, must_change_password=True)
    db.add(user)
    db.commit()
    return public_user(user)


@app.patch('/users/{user_id}')
def users_state(user_id: str, body: TeamUserState, auth: OWNER, db: DB):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, 'Пользователь не найден')
    if user.role == 'owner' and body.enabled is False:
        raise ValueError('Учётную запись владельца нельзя отключить')
    if body.enabled is not None:
        user.enabled = body.enabled
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.position is not None and user.role != 'owner':
        user.position = body.position
        if body.permissions is None: user.permissions = permission_preset(body.position)
    if body.permissions is not None and user.role != 'owner':
        allowed = {'data:read','assistant:use','wb:operate','ozon:operate','yandex:operate','drafts:publish'}
        user.permissions = [item for item in dict.fromkeys(body.permissions) if item in allowed]
    if body.store_ids is not None and user.role != 'owner':
        existing = set(db.scalars(select(StoreProfile.id)))
        user.store_ids = [item for item in dict.fromkeys(body.store_ids) if item in existing]
    if body.enabled is False:
        db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.commit()
    return public_user(user)


@app.delete('/users/{user_id}')
def users_delete(user_id: str, request: Request, auth: OWNER, db: DB):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, 'Пользователь не найден')
    if user.id == auth['user_id'] or user.role == 'owner':
        raise HTTPException(409, 'Аккаунт владельца и собственный аккаунт удалить нельзя')
    request.state.activity_details = {'event': 'user_deleted', 'target_username': user.username}
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    # Preserve authored drafts, rules and audit history; only remove account access.
    registration = db.scalar(select(RegistrationRequest).where(RegistrationRequest.username == user.username))
    if registration:
        registration.status = 'deleted'
    db.delete(user)
    db.commit()
    return {'ok': True}


@app.post('/users/{user_id}/password')
def users_password(user_id: str, body: TeamPassword, auth: OWNER, db: DB):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, 'Пользователь не найден')
    user.password_hash = PasswordHasher().hash(body.password)
    user.must_change_password = True
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.commit()
    return {'ok': True}


@app.post('/auth/password')
def own_password(body: OwnPassword, auth: AUTH, db: DB):
    user = db.get(User, auth['user_id'])
    try:
        PasswordHasher().verify(user.password_hash, body.current_password)
    except (VerificationError, InvalidHashError):
        raise HTTPException(401, 'Текущий пароль указан неверно') from None
    user.password_hash = PasswordHasher().hash(body.new_password)
    user.must_change_password = False
    if user.role == 'owner':
        set_setting(db, 'password_hash', user.password_hash)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.commit()
    return {'ok': True, 'login_required': True}


@app.get('/security')
def security(auth: OWNER, db: DB):
    audit = db.scalars(select(Audit).order_by(Audit.id.desc()).limit(30)).all()
    active = active_store(db, auth)
    return {'privacy_mode': 'SERVER_SHARED', 'safe_mode': setting(db, 'safe_mode', False), 'test_mode': setting(db, 'test_mode', True), 'cloud_ai': False, 'web_research': True, 'search_provider': setting(db, 'search_provider', 'duckduckgo'), 'search_configured': bool(db.get(Account, 'search:tavily')), 'telemetry': False, 'database': 'POSTGRESQL' if DATABASE_URL.startswith('postgresql') else 'SQLITE', 'token': 'ENCRYPTED' if db.get(Account, active) else 'NOT_CONNECTED', 'active_store': active, 'backend': 'server', 'model': MODEL, 'real_publish_enabled': REAL_PUBLISH, 'audit': [public(a) for a in audit], 'backups': sorted(p.name for p in (DATA / 'backups').glob('*.db'))[-10:]}


@app.get('/system/status')
def system_status(auth: AUTH, db: DB):
    schedule = setting(db, 'automation_schedule', {})
    latest = {}
    for kind in ('reviews', 'questions', 'products', 'drafts'):
        job = db.scalar(select(Job).where(Job.kind == kind, Job.status == 'completed').order_by(Job.created_at.desc()))
        latest[kind] = public(job) if job else None
    backups = sorted((DATA / 'backups').glob('*.db'), key=lambda path: path.stat().st_mtime, reverse=True)[:10]
    try: health = database_health()
    except Exception as error: health = {'status':'failed','detail':type(error).__name__}
    database_bytes = (db.scalar(select(func.pg_database_size(func.current_database()))) or 0) if DATABASE_URL.startswith('postgresql') else (DATABASE.stat().st_size if DATABASE.exists() else 0)
    return {
        'database_mb': round(database_bytes / 1024 / 1024, 1),
        'automation_enabled': bool(schedule.get('enabled')),
        'auto_replies_enabled': bool(schedule.get('enabled') and schedule.get('drafts_enabled')),
        'scheduler': setting(db, 'scheduler_status', {'state': 'waiting'}),
        'latest': latest,
        'active_jobs': db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(['queued', 'running']))) or 0,
        'backups': [{'name': path.name, 'size_mb': round(path.stat().st_size / 1024 / 1024, 1), 'created_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()} for path in backups],
        'database_health': health,
        'startup_recovery': SQLITE_STARTUP_HEALTH,
        'notifications_unread': db.scalar(select(func.count()).select_from(Notification).where(Notification.read == False)) or 0,
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
def performance_set(body: Performance, auth: OWNER, db: DB):
    from .model_router import ModelRouter
    if body.mode not in ModelRouter.PROFILES:
        raise ValueError('Неизвестный режим производительности')
    set_setting(db,'performance_mode',body.mode)
    db.commit()
    return {'mode':body.mode}


class AssistantEnabled(Payload):
    enabled: bool


@app.get('/settings/assistant')
def assistant_status(auth: AUTH, db: DB):
    return {'enabled': bool(setting(db, 'assistant_enabled', True))}


@app.patch('/settings/assistant')
def assistant_toggle(body: AssistantEnabled, auth: OWNER, db: DB):
    set_setting(db, 'assistant_enabled', body.enabled)
    if not body.enabled:
        schedule = setting(db, 'automation_schedule', {})
        schedule['drafts_enabled'] = False
        set_setting(db, 'automation_schedule', schedule)
        for job in db.scalars(select(Job).where(Job.kind.in_(['drafts','question_drafts','single_draft','single_question_draft']), Job.status.in_(['queued', 'running']))):
            job.cancel_requested = True
            if job.status == 'queued':
                job.status = 'cancelled'
    db.commit()
    return {'enabled': body.enabled}


@app.patch('/settings/modes')
def modes(body: Modes, auth: OWNER, db: DB):
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


class TelegramInput(Payload):
    bot_token: str = Field(default='', max_length=4096)
    chat_id: str = Field(default='', max_length=100)
    enabled: bool = False


class StoreInput(Payload):
    name: str = Field(min_length=2, max_length=100)
    provider: Literal['wb','ozon','yandex'] = 'wb'
    client_id: str = Field(default='', max_length=200)
    business_id: str = Field(default='', max_length=100)


class StoreConnection(Payload):
    token: str = Field(min_length=10, max_length=4096)
    client_id: str = Field(default='', max_length=200)
    business_id: str = Field(default='', max_length=100)


class ScheduleInput(Payload):
    enabled: bool = False
    reviews_hours: int = Field(default=3, ge=1, le=168)
    products_hours: int = Field(default=24, ge=1, le=720)
    questions_hours: int = Field(default=3, ge=1, le=168)
    backup_hours: int = Field(default=24, ge=1, le=720)
    drafts_enabled: bool = False
    drafts_hours: int = Field(default=6, ge=1, le=168)
    question_drafts_enabled: bool = False
    auto_propose: bool = False
    min_quality: int = Field(default=96, ge=70, le=100)
    max_proposals: int = Field(default=100, ge=1, le=100)
    drafts_days: int = Field(default=7, ge=1, le=90)


@app.post('/settings/wb-token')
def token(body: Token, auth: OWNER, db: DB):
    active = active_store(db, auth)
    # Re-saving a rotated or restored token must not be blocked by data that already
    # belongs to this explicit store profile. This also prevents the shared team
    # connection from appearing to "fall off" after the first synchronization.
    db.merge(Account(id=active, encrypted_token=encrypt_secret(body.token)))
    db.commit()
    return {'token': 'ENCRYPTED'}


@app.post('/settings/wb-test')
async def wb_test(auth: WB_OPERATOR, db: DB):
    store_id = active_store(db, auth)
    can_operate_store(auth, db.get(StoreProfile, store_id))
    await guard.request('wb', 'ping', token=wb_token(db, store_id))
    return {'ok': True}


@app.get('/settings/search')
def search_settings(auth: AUTH, db: DB):
    provider = setting(db, 'search_provider', 'duckduckgo')
    return {'provider': provider, 'configured': bool(db.get(Account, 'search:tavily')), 'status': 'Надёжный API подключён' if provider == 'tavily' and db.get(Account, 'search:tavily') else 'Резервный поиск без гарантии доступности'}


@app.patch('/settings/search')
def search_settings_update(body: SearchProviderInput, auth: OWNER, db: DB):
    if body.provider not in ('duckduckgo', 'tavily'):
        raise ValueError('Поддерживаются DuckDuckGo и Tavily')
    if body.provider == 'tavily' and not body.api_key and not db.get(Account, 'search:tavily'):
        raise ValueError('Для Tavily нужен API-ключ')
    if body.api_key:
        db.merge(Account(id='search:tavily', encrypted_token=encrypt_secret(body.api_key)))
    set_setting(db, 'search_provider', body.provider)
    db.commit()
    return search_settings(auth, db)


def add_notification(db, kind, title, message, severity='info', store_id='', user_id=''):
    item = Notification(id=str(uuid4()), kind=kind, title=title[:200], message=message[:2000], severity=severity, store_id=store_id, user_id=user_id)
    db.add(item)
    return item


@app.get('/notifications')
def notifications(auth: AUTH, db: DB, unread: bool = False):
    query = select(Notification).where((Notification.user_id == '') | (Notification.user_id == auth['user_id']))
    if unread: query = query.where(Notification.read == False)
    items = list(db.scalars(query.order_by(Notification.created_at.desc()).limit(100)))
    return {'unread': db.scalar(select(func.count()).select_from(Notification).where(Notification.read == False, (Notification.user_id == '') | (Notification.user_id == auth['user_id']))) or 0, 'items':[public(item) for item in items]}


@app.post('/notifications/read-all')
def notifications_read(auth: AUTH, db: DB):
    for item in db.scalars(select(Notification).where(Notification.read == False, (Notification.user_id == '') | (Notification.user_id == auth['user_id']))): item.read = True
    db.commit(); return {'ok':True}


@app.get('/settings/telegram')
def telegram_get(auth: OWNER, db: DB):
    value = setting(db, 'telegram', {})
    return {'enabled':bool(value.get('enabled')), 'chat_id':value.get('chat_id',''), 'configured':bool(db.get(Account,'telegram:bot'))}


async def send_telegram(db, text_value):
    value, row = setting(db, 'telegram', {}), db.get(Account, 'telegram:bot')
    if not value.get('enabled') or not row: return False
    token = decrypt_secret(row.encrypted_token)
    await guard.request('telegram','send',url=f'https://api.telegram.org/bot{token}/sendMessage',body={'chat_id':value.get('chat_id'), 'text':text_value[:4000]})
    return True


@app.patch('/settings/telegram')
def telegram_set(body: TelegramInput, auth: OWNER, db: DB):
    if body.enabled and not body.chat_id.strip(): raise ValueError('Укажите Chat ID Telegram')
    if body.bot_token: db.merge(Account(id='telegram:bot', encrypted_token=encrypt_secret(body.bot_token.strip())))
    if body.enabled and not (body.bot_token or db.get(Account,'telegram:bot')): raise ValueError('Укажите токен Telegram-бота')
    set_setting(db, 'telegram', {'enabled':body.enabled,'chat_id':body.chat_id.strip()}); db.commit()
    return telegram_get(auth, db)


@app.post('/settings/telegram/test')
async def telegram_test(auth: OWNER, db: DB):
    if not await send_telegram(db, 'Ассистент маркетплейсов: уведомления подключены.'): raise ValueError('Сначала включите Telegram и сохраните настройки')
    return {'ok':True}


@app.get('/stores')
def stores(auth: AUTH, db: DB):
    allowed = auth.get('store_ids') or []
    rows = list(db.scalars(select(StoreProfile).order_by(StoreProfile.created_at)))
    assigned = lambda store: auth['role'] == 'owner' or '*' in allowed or store.id in allowed
    active = active_store(db, auth) if rows else ''
    return {'active': active, 'items': [{**public(s), 'config': {k:v for k,v in (s.config or {}).items() if k in ('client_id','business_id')} if assigned(s) else {},
                                      'connected': bool(db.get(Account, s.id)), 'accessible': True,
                                      'can_operate': assigned(s) and ('*' in auth.get('permissions', []) or s.provider + ':operate' in auth.get('permissions', [])),
                                      'reviews': db.scalar(select(func.count()).select_from(Review).where(Review.wb_account_id == s.id)) or 0} for s in rows]}


@app.post('/stores')
def create_store(body: StoreInput, auth: OWNER, db: DB):
    store = StoreProfile(id='store-' + uuid4().hex[:12], name=body.name.strip(), provider=body.provider, config={'client_id':body.client_id.strip(), 'business_id':body.business_id.strip()})
    db.add(store)
    db.commit()
    return public(store)


@app.post('/stores/{store_id}/activate')
def activate_store(store_id: str, auth: AUTH, db: DB):
    if not db.get(StoreProfile, store_id):
        raise HTTPException(404)
    set_setting(db, 'active_store:' + auth['user_id'], store_id)
    if auth['role'] == 'owner': set_setting(db, 'active_store', store_id)
    db.commit()
    return {'active': store_id}


@app.post('/stores/{store_id}/connection')
def connect_store(store_id: str, body: StoreConnection, auth: OWNER, db: DB):
    store = db.get(StoreProfile, store_id)
    if not store: raise HTTPException(404, 'Магазин не найден')
    if store.provider == 'ozon' and not body.client_id.strip():
        raise ValueError('Укажите Client-Id Ozon')
    if store.provider == 'yandex' and not body.business_id.strip().isdigit():
        raise ValueError('Укажите числовой Business ID Яндекс Маркета')
    store.config = {'client_id':body.client_id.strip(), 'business_id':body.business_id.strip()}
    db.merge(Account(id=store_id, encrypted_token=encrypt_secret(body.token)))
    db.commit()
    return {'ok':True, 'provider':store.provider, 'token':'ENCRYPTED'}


@app.post('/stores/{store_id}/test')
async def test_store(store_id: str, auth: AUTH, db: DB):
    store = db.get(StoreProfile, store_id)
    if not store: raise HTTPException(404, 'Магазин не найден')
    can_operate_store(auth, store)
    if store.provider == 'wb':
        await guard.request('wb','ping',token=wb_token(db,store.id))
    else:
        await provider_for(store).test(db, store.id)
    return {'ok':True, 'provider':store.provider}


@app.get('/settings/schedule')
def schedule_get(auth: AUTH, db: DB):
    defaults = {'enabled': False, 'reviews_hours': 3, 'questions_hours': 3, 'products_hours': 24, 'backup_hours': 24, 'drafts_enabled': False, 'question_drafts_enabled': False, 'drafts_hours': 6, 'auto_propose': False, 'min_quality': 96, 'max_proposals': 100, 'drafts_days': 7}
    return {**defaults, **setting(db, 'automation_schedule', {})}


@app.patch('/settings/schedule')
def schedule_set(body: ScheduleInput, auth: OWNER, db: DB):
    current = setting(db, 'automation_schedule', {})
    value = {**current, **body.model_dump()}
    if value.get('drafts_enabled'):
        value['drafts_since'] = (datetime.now(timezone.utc) - timedelta(days=int(value.get('drafts_days', 7)))).isoformat()
    set_setting(db, 'automation_schedule', value)
    db.commit()
    return value


def automated_draft_scope(account_id):
    return (Job.kind=='drafts') & Job.result['manual_batch'].as_boolean().is_not(True) & (func.coalesce(Job.result['account_id'].as_string(),'owner')==account_id)


def auto_replies_status(db,auth=None):
    schedule = schedule_get(None, db)
    enabled = bool(schedule.get('enabled') and schedule.get('drafts_enabled'))
    account_id=active_store(db,auth) if auth else setting(db,'active_store','owner')
    current_job = db.scalar(select(Job).where(automated_draft_scope(account_id), Job.status.in_(['queued', 'running'])).order_by(Job.created_at.desc()))
    last_job = db.scalar(select(Job).where(automated_draft_scope(account_id), Job.status.not_in(['queued', 'running'])).order_by(Job.created_at.desc()))
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
    return auto_replies_status(db,auth)


@app.post('/auto-replies/start')
async def start_auto_replies(background: BackgroundTasks, auth: MARKET_OPERATOR, db: DB):
    if not setting(db, 'assistant_enabled', True):
        raise ValueError('Сначала включите ассистента')
    store = db.get(StoreProfile, active_store(db, auth))
    if not store: raise HTTPException(404, 'Магазин не найден')
    can_operate_store(auth, store)
    schedule = schedule_get(auth, db)
    since = (datetime.now(timezone.utc) - timedelta(days=int(schedule.get('drafts_days', 7)))).isoformat()
    schedule.update({'enabled': True, 'drafts_enabled': True, 'last_drafts': time.time(), 'drafts_since': since})
    set_setting(db, 'automation_schedule', schedule)
    job = db.scalar(select(Job).where(automated_draft_scope(active_store(db,auth)), Job.status.in_(['queued', 'running'])).order_by(Job.created_at.desc()))
    if not job:
        active = active_store(db, auth)
        job = Job(id=str(uuid4()), kind='drafts', priority=50, result={'account_id': active, 'since': since, 'message': 'Автоответы запущены: подготавливаем новые отзывы'})
        db.add(job)
    db.commit()
    result = auto_replies_status(db,auth)
    result['job'] = public(job)
    background.add_task(run_job_queue)
    return result


@app.post('/auto-replies/pause')
def pause_auto_replies(auth: MARKET_OPERATOR, db: DB):
    store = db.get(StoreProfile, active_store(db, auth))
    if not store: raise HTTPException(404, 'Магазин не найден')
    can_operate_store(auth, store)
    schedule = schedule_get(auth, db)
    schedule['drafts_enabled'] = False
    set_setting(db, 'automation_schedule', schedule)
    for job in db.scalars(select(Job).where(automated_draft_scope(active_store(db,auth)), Job.status.in_(['queued', 'running']))):
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
            started_clock = time.monotonic()
            queue_seconds = max(0, time.time() - datetime.fromisoformat(job.created_at).timestamp())
            manual_batch = bool((job.result or {}).get('manual_batch'))
            requested_by = (job.result or {}).get('requested_by', '')
            job_metadata = {key: job.result[key] for key in ('operation_key', 'operation_requested_ids', 'skipped') if key in (job.result or {})}
            job.result = {**(job.result or {}), 'started_at': now()}
            job.status, job.progress, job.attempts = 'running', 1, job.attempts + 1
            kind = job.kind
            account_id = (job.result or {}).get('account_id') or setting(db, 'active_store', 'owner')
            start_checkpoint = (job.result or {}).get('checkpoint') or {}
            db.commit()
            def update_progress(value):
                db.refresh(job)
                job.progress = value
                db.commit()
            def cancelled():
                db.refresh(job)
                return job.cancel_requested
            def save_checkpoint(value):
                db.refresh(job)
                job.result = {**(job.result or {}), 'checkpoint': value, 'account_id': account_id,
                              'message': f'Сохранён прогресс: {value.get("processed", 0)} записей'}
                db.commit()
            try:
                if kind in ('drafts', 'question_drafts', 'single_draft', 'single_question_draft'):
                    since = (job.result or {}).get('since', '')
                    is_questions = kind in ('question_drafts', 'single_question_draft')
                    is_single = kind in ('single_draft', 'single_question_draft')
                    conditions = [
                        Review.wb_account_id == account_id,
                        Review.is_answered == False,
                        Review.status != 'publishing',
                        Review.marketplace.like('%_question') if is_questions else ~Review.marketplace.like('%_question'),
                    ]
                    if not is_single:
                        conditions.append(Draft.id.is_(None))
                    selected_ids = list((job.result or {}).get('selected_ids') or [])
                    if selected_ids:
                        conditions.append(Review.id.in_(selected_ids))
                    if since:
                        conditions.append(Review.created_at >= since)
                    ids = list(db.scalars(
                        select(Review.id).outerjoin(Draft, Draft.review_id == Review.id).where(
                            *conditions,
                        ).order_by(Review.created_at.desc(), Review.id)
                    ))
                    total, processed, failed, failures = len(ids), 0, 0, []
                    single_draft = None
                    job.result = {**job.result, 'total': total, 'processed': 0, 'failed': 0}
                    db.commit()
                    for index, review_id in enumerate(ids, 1):
                        if cancelled():
                            raise asyncio.CancelledError
                        try:
                            single_draft = await generate_draft(db, review_id, (job.result or {}).get('instruction', '') if is_single else '')
                            if requested_by:
                                stored_draft = db.get(Draft, single_draft['id'])
                                if not stored_draft.created_by:
                                    stored_draft.created_by = requested_by
                                stored_draft.updated_by = requested_by
                                db.commit()
                                single_draft = draft_public(db, stored_draft)
                            processed += 1
                        except Exception as error:
                            db.rollback()
                            if is_single:
                                raise
                            failed += 1
                            if len(failures) < 20:
                                failures.append({'review_id': review_id, 'reason': (str(error) or type(error).__name__)[:300]})
                        job.result = {**(job.result or {}), 'processed': processed, 'failed': failed, 'total': total, 'failures': failures}
                        db.flush()
                        update_progress(round(index / max(1, total) * 100))
                    schedule = setting(db, 'automation_schedule', {})
                    proposal_id = None
                    if schedule.get('auto_propose') and not is_questions and not is_single and not manual_batch:
                        minimum = max(70, min(100, int(schedule.get('min_quality', 96))))
                        limit = max(1, min(100, int(schedule.get('max_proposals', 100))))
                        candidate_rows = db.execute(
                            select(Review.id, Draft.quality).join(Draft, Draft.review_id == Review.id).where(
                                Review.wb_account_id == account_id,
                                Review.is_answered == False,
                                Review.status == 'draft_ready',
                                Review.rating >= 4,
                                Review.manual == False,
                            ).order_by(Review.created_at.desc()).limit(limit * 5)
                        ).all()
                        candidates = [rid for rid, quality in candidate_rows if (quality or {}).get('passed') and int((quality or {}).get('score', 0)) >= minimum][:limit]
                        if candidates:
                            proposal_id = propose_publish(db, candidates)['id']
                    job = db.get(Job, job_id)
                    if is_single and (total != 1 or single_draft is None):
                        raise ValueError('Отзыв или вопрос изменился. Обновите страницу')
                    job.status, job.progress = 'completed', 100
                    job.result = {
                        'processed': processed,
                        'failed': failed,
                        'total': total,
                        'account_id': account_id,
                        'since': since,
                        'failures': failures,
                        'message': f'Подготовлено ответов на вопросы: {processed}. Ошибок: {failed}.' if is_questions else f'Подготовлено черновиков: {processed}. Требуют ручной проверки: {failed}.',
                        'proposal_id': proposal_id, 'selected_ids': selected_ids, 'manual_batch': manual_batch, 'requested_by': requested_by,
                        **({'draft': single_draft} if is_single else {}),
                    }
                else:
                    store = db.get(StoreProfile, account_id)
                    provider = provider_for(store) if store else None
                    if not provider:
                        raise ValueError('Провайдер магазина не поддерживается')
                    if kind == 'products' and store.provider != 'wb':
                        count = 0  # Ozon and Yandex products are learned from their review payloads.
                        job.result = {'message':'Товары этого маркетплейса обновляются вместе с отзывами'}
                        operation = None
                    else:
                        operation = provider.sync_products if kind == 'products' else provider.sync_questions if kind == 'questions' else provider.sync_reviews
                    try:
                        if operation: count = await operation(db, account_id, update_progress, cancelled, resume=start_checkpoint, checkpoint=save_checkpoint)
                    except TypeError as exc:
                        if 'unexpected keyword argument' not in str(exc):
                            raise
                        try:
                            count = await operation(db, account_id, update_progress, cancelled)
                        except TypeError as legacy_exc:
                            if 'positional' not in str(legacy_exc):
                                raise
                            count = await operation(db)
                    job.status, job.progress, job.result = 'completed', 100, {'processed':count, 'account_id': account_id, 'finished_at': now()}
                    if kind == 'reviews':
                        negative = db.scalar(select(func.count()).select_from(Review).where(Review.wb_account_id == account_id, Review.is_answered == False, Review.rating <= 2)) or 0
                        if negative:
                            add_notification(db, 'negative_reviews', 'Негативные отзывы требуют внимания', f'Без ответа осталось отзывов с оценкой 1–2★: {negative}.', 'warning', account_id)
                    schedule = setting(db, 'automation_schedule', {})
                    if kind == 'reviews' and schedule.get('enabled') and schedule.get('drafts_enabled') and not db.scalar(select(Job).where(Job.kind == 'drafts', Job.status.in_(['queued', 'running']))):
                        db.add(Job(id=str(uuid4()), kind='drafts', priority=250, result={'account_id': account_id, 'since': schedule.get('drafts_since', ''), 'scheduled': True, 'message': 'Подготовка ответов после получения новых отзывов'}))
                        schedule['last_drafts'] = time.time()
                        set_setting(db, 'automation_schedule', schedule)
                    if kind == 'questions' and schedule.get('enabled') and schedule.get('question_drafts_enabled') and setting(db, 'assistant_enabled', True) and not db.scalar(select(Job).where(Job.kind == 'question_drafts', Job.status.in_(['queued', 'running']))):
                        db.add(Job(id=str(uuid4()), kind='question_drafts', priority=250, result={'account_id': account_id, 'scheduled': True, 'message': 'Подготовка ответов после получения новых вопросов'}))
                        schedule['last_question_drafts'] = time.time()
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
                    default_message = 'Автоответы не завершены. Проверьте локальную модель и повторите задание.' if kind in ('drafts','question_drafts','single_draft','single_question_draft') else 'Синхронизация не завершена.'
                    detail = (str(error) or type(error).__name__)[:500]
                    message = f'{default_message} {detail}'
                    job.status, job.result = 'failed', {**(job.result or {}), 'message':message, 'account_id': account_id, 'error_type': type(error).__name__, 'detail': detail, 'finished_at': now()}
                    add_notification(db, 'job_failed', 'Задание завершилось с ошибкой', message, 'error', account_id)
            job.result = {**job_metadata, **(job.result or {}), 'timing': {'queue_seconds': round(queue_seconds, 2), 'processing_seconds': round(time.monotonic() - started_clock, 2)}, 'finished_at': now()}
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
                current = time.time()
                # Database protection must keep running even when business
                # automation is paused. Otherwise a paused shop receives no
                # integrity checks and no private recovery snapshots.
                if current - float(schedule.get('last_database_check', 0)) >= 3600:
                    health = await asyncio.to_thread(database_health)
                    schedule['last_database_check'] = current
                    if health.get('status') != 'ok':
                        add_notification(db, 'database', 'Проверка базы не пройдена', str(health), 'error')
                if DATABASE_URL.startswith('sqlite:') and current - float(schedule.get('last_recovery_snapshot', 0)) >= 6 * 3600:
                    async with maintenance_lock:
                        await asyncio.to_thread(recovery_snapshot, 10)
                    schedule['last_recovery_snapshot'] = current
                if not schedule.get('enabled'):
                    set_setting(db, 'scheduler_status', {'state': 'ok', 'checked_at': datetime.now(timezone.utc).isoformat(), 'last_error': ''})
                    set_setting(db, 'automation_schedule', schedule)
                    db.commit()
                    continue
                connected_stores = [store for store in db.scalars(select(StoreProfile).order_by(StoreProfile.created_at)) if db.get(Account, store.id)]
                for kind, hours in (('products', int(schedule.get('products_hours', 24))), ('reviews', int(schedule.get('reviews_hours', 3))), ('questions', int(schedule.get('questions_hours', 3)))):
                    if db.scalar(select(Job).where(Job.kind == kind, Job.status.in_(['queued', 'running']))):
                        continue
                    for store in connected_stores:
                        if kind == 'products' and store.provider != 'wb':
                            continue
                        last_key = f'last_{kind}:{store.id}'
                        last = float(schedule.get(last_key, schedule.get('last_' + kind, 0) if store.id == 'owner' else 0))
                        if current - last >= max(1, hours) * 3600:
                            db.add(Job(id=str(uuid4()), kind=kind, priority=200, result={'account_id': store.id, 'scheduled': True}))
                            schedule[last_key] = current
                            break
                for kind, enabled in (('drafts', schedule.get('drafts_enabled')), ('question_drafts', schedule.get('question_drafts_enabled'))):
                    if not enabled or not setting(db, 'assistant_enabled', True):
                        continue
                    if db.scalar(select(Job).where(Job.kind == kind, Job.status.in_(['queued', 'running']))):
                        continue
                    hours = max(1, int(schedule.get('drafts_hours', 6)))
                    for store in connected_stores:
                        last_key = f'last_{kind}:{store.id}'
                        last = float(schedule.get(last_key, schedule.get('last_' + kind, 0) if store.id == 'owner' else 0))
                        if current - last >= hours * 3600:
                            result = {'account_id': store.id, 'scheduled': True, 'message': 'Плановая подготовка ответов на вопросы' if kind == 'question_drafts' else 'Плановая подготовка автоответов'}
                            if kind == 'drafts': result['since'] = schedule.get('drafts_since', '')
                            db.add(Job(id=str(uuid4()), kind=kind, priority=250, result=result))
                            schedule[last_key] = current
                            break
                if current - float(schedule.get('last_backup', 0)) >= max(1, int(schedule.get('backup_hours', 24))) * 3600:
                    async with maintenance_lock:
                        await asyncio.to_thread(backup)
                        if DATABASE_URL.startswith('sqlite:'): await asyncio.to_thread(recovery_snapshot)
                    schedule['last_backup'] = current
                portable_sync = setting(db, 'portable_sync', {'enabled': False})
                sync_account = db.get(Account, 'portable:sync')
                if portable_sync.get('enabled') and sync_account and current - float(portable_sync.get('last_sync', 0)) >= max(1, int(portable_sync.get('hours', 24))) * 3600:
                    destination = Path(str(portable_sync.get('directory', ''))).expanduser().resolve()
                    if not destination.is_dir():
                        raise ValueError('Папка синхронизации недоступна')
                    async with maintenance_lock:
                        password = decrypt_secret(sync_account.encrypted_token)
                        export = await asyncio.to_thread(portable_backup, password)
                        copied = await asyncio.to_thread(shutil.copy2, export, destination / export.name)
                        verified = await asyncio.to_thread(portable_verify, copied, password)
                    for obsolete in sorted(destination.glob('wb-assistant-*.wbai'), key=lambda path:path.stat().st_mtime, reverse=True)[10:]:
                        obsolete.unlink(missing_ok=True)
                    portable_sync.update(last_sync=current,last_verified=current,last_verified_counts={'reviews':verified['reviews'],'drafts':verified['drafts']},last_file=Path(copied).name,last_error='')
                    set_setting(db, 'portable_sync', portable_sync)
                set_setting(db, 'scheduler_status', {'state': 'ok', 'checked_at': datetime.now(timezone.utc).isoformat(), 'last_error': ''})
                set_setting(db, 'automation_schedule', schedule)
                db.commit()
                pending_notifications = list(db.scalars(select(Notification).where(Notification.telegram_sent == False, Notification.severity.in_(['warning','error'])).order_by(Notification.created_at).limit(10)))
                for notification in pending_notifications:
                    try:
                        if await send_telegram(db, f'{notification.title}\n{notification.message}'):
                            notification.telegram_sent = True
                            db.commit()
                    except ValueError:
                        break
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
    if kind not in ('products','reviews','questions'):
        raise HTTPException(404)
    active = active_store(db, auth)
    store = db.get(StoreProfile, active)
    if not store: raise HTTPException(404, 'Магазин не найден')
    can_operate_store(auth, store)
    if store.provider == 'wb': wb_token(db, active)
    elif not db.get(Account, active): raise ValueError('Сначала подключите API выбранного магазина')
    job = Job(id=str(uuid4()), kind=kind, result={'account_id': active})
    db.add(job)
    db.commit()
    background.add_task(run_job_queue)
    return public(job)


@app.post('/jobs/drafts')
async def schedule_all_drafts(background: BackgroundTasks, auth: AUTH, db: DB):
    can(auth, 'assistant:use')
    active = active_store(db, auth)
    store = db.get(StoreProfile, active)
    if not store: raise HTTPException(404, 'Магазин не найден')
    can_operate_store(auth, store)
    existing = db.scalar(select(Job).where(Job.kind == 'drafts', Job.status.in_(['queued', 'running'])))
    if existing:
        return public(existing)
    job = Job(id=str(uuid4()), kind='drafts', priority=50, result={'account_id': active, 'message': 'Подготовка ответов поставлена в очередь'})
    db.add(job)
    db.commit()
    background.add_task(run_job_queue)
    return public(job)


selected_drafts_lock = asyncio.Lock()


class SelectedDraftsInput(Payload):
    review_ids: list[str] = Field(min_length=1, max_length=100)
    operation_key: str = Field(default='', max_length=80)


@app.post('/jobs/selected-drafts')
async def schedule_selected_drafts(body: SelectedDraftsInput, background: BackgroundTasks, auth: MARKET_OPERATOR, db: DB):
    async with selected_drafts_lock:
        can(auth, 'assistant:use')
        if not setting(db, 'assistant_enabled', True):
            raise ValueError('Сначала включите ассистента')
        active = active_store(db, auth)
        store = db.get(StoreProfile, active)
        if not store:
            raise HTTPException(404, 'Магазин не найден')
        can_operate_store(auth, store)
        ids = list(dict.fromkeys(body.review_ids))
        rows = list(db.scalars(select(Review).where(Review.id.in_(ids), Review.wb_account_id == active, ~Review.marketplace.like('%_question'))))
        if len(rows) != len(ids):
            raise HTTPException(404, 'Один из выбранных отзывов недоступен')
        if body.operation_key:
            previous = db.scalar(select(Job).where(Job.kind == 'drafts', Job.result['operation_key'].as_string() == body.operation_key, Job.result['requested_by'].as_string() == auth['user_id'], Job.result['account_id'].as_string() == active))
            if previous:
                if sorted((previous.result or {}).get('operation_requested_ids') or []) != sorted(ids):
                    raise HTTPException(409, 'Этот запрос уже использован для другой подборки')
                return public(previous)
        existing_ids = set(db.scalars(select(Draft.review_id).where(Draft.review_id.in_(ids))))
        eligible = sorted(row.id for row in rows if not row.is_answered and row.status != 'publishing' and row.id not in existing_ids)
        for job in db.scalars(select(Job).where(Job.kind == 'drafts', Job.status.in_(['queued', 'running']), Job.result['account_id'].as_string() == active)):
            if (job.result or {}).get('manual_batch') and sorted((job.result or {}).get('selected_ids') or []) == eligible:
                return public(job)
        if not eligible:
            raise HTTPException(409, 'У выбранных отзывов уже есть ответы или черновики. Ваши правки сохранены')
        job = Job(id=str(uuid4()), kind='drafts', priority=45, max_attempts=1,
                  result={'account_id': active, 'selected_ids': eligible, 'requested_by': auth['user_id'],
                          'manual_batch': True, 'operation_key': body.operation_key, 'operation_requested_ids': sorted(ids), 'total': len(eligible), 'processed': 0, 'failed': 0,
                          'skipped': len(ids) - len(eligible), 'message': 'Выбранные отзывы поставлены в очередь'})
        db.add(job)
        db.commit()
        background.add_task(run_job_queue)
        return public(job)


@app.get('/generation/metrics')
def reply_generation_metrics(auth: OWNER, db: DB):
    return generation_metrics(db, active_store(db, auth))


@app.get('/jobs/drafts/preview')
def preview_all_drafts(auth: AUTH, db: DB):
    active = active_store(db, auth)
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
    rows = list(db.scalars(select(Job).order_by(Job.created_at.desc()).limit(100)))
    if auth['role'] != 'owner':
        active = active_store(db, auth)
        rows = [job for job in rows if (job.result or {}).get('account_id') == active]
    return [public(j) for j in rows]


@app.post('/jobs/{jid}/cancel')
def cancel_job(jid: str, auth: MARKET_OPERATOR, db: DB):
    job = db.get(Job, jid)
    if not job or job.status not in ('queued', 'running'):
        raise ValueError('Это задание уже завершено')
    if auth['role'] != 'owner' and (job.result or {}).get('account_id') != active_store(db, auth):
        raise HTTPException(404, 'Задание не найдено')
    job_store = (job.result or {}).get('account_id')
    if job_store:
        can_operate_store(auth, db.get(StoreProfile, job_store))
    elif auth['role'] != 'owner':
        raise HTTPException(403, 'Это задание доступно только владельцу')
    job.cancel_requested = True
    if job.status == 'queued':
        job.status = 'cancelled'
    db.commit()
    return public(job)


@app.post('/jobs/{jid}/retry')
async def retry_job(jid: str, background: BackgroundTasks, auth: MARKET_OPERATOR, db: DB):
    job = db.get(Job, jid)
    if not job or job.status not in ('failed', 'interrupted', 'cancelled'):
        raise ValueError('Повтор доступен только для незавершённого задания')
    if auth['role'] != 'owner' and (job.result or {}).get('account_id') != active_store(db, auth):
        raise HTTPException(404, 'Задание не найдено')
    job_store = (job.result or {}).get('account_id')
    if job_store:
        can_operate_store(auth, db.get(StoreProfile, job_store))
    elif auth['role'] != 'owner':
        raise HTTPException(403, 'Это задание доступно только владельцу')
    job.status, job.cancel_requested, job.progress, job.attempts = 'queued', False, 0, 0
    db.commit()
    background.add_task(run_job_queue)
    return public(job)


@app.get('/jobs/{jid}')
def get_job(jid: str, auth: AUTH, db: DB):
    job = db.get(Job,jid)
    if not job:
        raise HTTPException(404)
    if auth['role'] != 'owner' and (job.result or {}).get('account_id') != active_store(db, auth):
        raise HTTPException(404)
    return public(job)


@app.get('/sync/status')
def sync_status(auth: AUTH, db: DB):
    jobs = list(db.scalars(select(Job).where(Job.kind.in_(['products', 'reviews', 'questions'])).order_by(Job.created_at.desc()).limit(1000)))
    result = []
    for store in db.scalars(select(StoreProfile).order_by(StoreProfile.created_at)):
        kinds = {}
        for kind in ('products', 'reviews', 'questions'):
            matching = [job for job in jobs if job.kind == kind and (job.result or {}).get('account_id') == store.id]
            latest = matching[0] if matching else None
            success = next((job for job in matching if job.status == 'completed'), None)
            kinds[kind] = {
                'state': latest.status if latest else 'never',
                'progress': latest.progress if latest else 0,
                'message': (latest.result or {}).get('message', '') if latest else '',
                'processed': (latest.result or {}).get('processed', (latest.result or {}).get('checkpoint', {}).get('processed', 0)) if latest else 0,
                'last_success_at': (success.result or {}).get('finished_at', success.created_at) if success else '',
                'last_success_count': (success.result or {}).get('processed', 0) if success else 0,
                'job_id': latest.id if latest else '',
            }
        result.append({'store_id': store.id, 'store_name': store.name, 'provider': store.provider, 'kinds': kinds})
    return {'stores': result}


@app.post('/sync/{kind}')
async def sync(kind: str, auth: MARKET_OPERATOR, db: DB):
    store = db.get(StoreProfile, active_store(db, auth))
    if not store: raise HTTPException(404, 'Магазин не найден')
    can_operate_store(auth, store)
    if kind == 'products' and store.provider != 'wb':
        raise ValueError('Отдельная загрузка каталога для этого маркетплейса пока недоступна')
    provider = provider_for(store)
    if sync_lock.locked():
        raise ValueError('Синхронизация уже выполняется')
    async with sync_lock:
        if kind == 'products':
            count = await provider.sync_products(db, store.id)
        elif kind == 'reviews':
            count = await provider.sync_reviews(db, store.id)
        elif kind == 'questions':
            count = await provider.sync_questions(db, store.id)
        else:
            raise HTTPException(404)
    return {'processed': count}


@app.get('/reviews')
def reviews(auth: AUTH, db: DB, q: str = '', unanswered: bool = False, max_rating: int = 5, days: int = 0, product_id: str = '', limit: int = 100, attention: bool = False):
    return reviews_search(db, q, unanswered, max_rating, days, product_id, limit, account_id=active_store(db, auth), attention=attention)


@app.get('/reviews/page')
def reviews_paginated(auth: AUTH, db: DB, q: str = '', unanswered: bool = False, max_rating: int = 5, days: int = 0, product_id: str = '', limit: int = 30, offset: int = 0, attention: bool = False, view: Literal['all', 'positive', 'negative', 'rating_only', 'manual', 'wrong_item', 'no_effect', 'odor', 'context'] = 'all', review_ids: str = ''):
    return reviews_page(db, q=q, unanswered=unanswered, max_rating=max_rating, days=days, product_id=product_id, limit=limit, offset=offset, account_id=active_store(db, auth), attention=attention, view=view, review_ids=review_ids)


@app.get('/reviews/counts')
def review_counts(auth: AUTH, db: DB, q: str = ''):
    return review_filter_counts(db, q=q, account_id=active_store(db, auth))


@app.get('/drafts/{did}')
def current_draft(did: str, auth: AUTH, db: DB):
    item = db.get(Draft, did)
    review = db.get(Review, item.review_id) if item else None
    if not review or review.wb_account_id != active_store(db, auth):
        raise HTTPException(404, 'Черновик не найден')
    return draft_public(db, item)


@app.get('/questions/page')
def questions_paginated(auth: AUTH, db: DB, q: str = '', unanswered: bool = False, status: str = '', kind: str = '', limit: int = 30, offset: int = 0):
    limit, offset = min(200, max(1, limit)), max(0, offset)
    conditions = [Review.wb_account_id == active_store(db, auth), Review.marketplace.like('%_question')]
    if q:
        conditions.append(Review.text.contains(q, autoescape=True) | Product.name.contains(q, autoescape=True) | Product.brand.contains(q, autoescape=True))
    if unanswered:
        conditions.append(Review.is_answered == False)
    if status == 'answered':
        conditions.append(Review.is_answered == True)
    elif status == 'draft':
        conditions.extend((Review.is_answered == False, Review.status.in_(['draft_ready','pending_confirmation'])))
    query = select(Review).join(Product).where(*conditions)
    ordered = query.order_by(Review.created_at.desc(), Review.id)
    if kind:
        matching = [row for row in db.scalars(ordered) if question_intent(row.text) == kind]
        total = len(matching)
        rows = matching[offset:offset + limit]
    else:
        total = db.scalar(select(func.count()).select_from(Review).join(Product).where(*conditions)) or 0
        rows = list(db.scalars(ordered.offset(offset).limit(limit)))
    items = review_items(db, rows)
    for item in items:
        item['question_type'] = question_intent(item['text'])
    return {'items': items, 'total': total, 'limit': limit, 'offset': offset, 'has_more': offset + len(items) < total}


@app.get('/questions/stats')
def question_stats(auth: AUTH, db: DB):
    active = active_store(db, auth)
    base = [Review.wb_account_id == active, Review.marketplace.like('%_question')]
    rows = list(db.scalars(select(Review).where(*base)))
    types = {}
    for row in rows:
        label = question_intent(row.text)
        types[label] = types.get(label, 0) + 1
    return {
        'total': len(rows),
        'unanswered': sum(not row.is_answered for row in rows),
        'drafts': sum(row.status in ('draft_ready','pending_confirmation') and not row.is_answered for row in rows),
        'answered': sum(row.is_answered for row in rows),
        'types': types,
    }


@app.get('/products')
def products(auth: AUTH, db: DB, q: str = ''):
    store = db.get(StoreProfile, active_store(db, auth))
    scope = product_scope(store)
    return [public(p) for p in db.scalars(select(Product).where(scope, Product.name.contains(q, autoescape=True) | Product.brand.contains(q, autoescape=True)).limit(200))]


@app.get('/products/knowledge')
def product_knowledge_report(auth: AUTH,db: DB):
    return knowledge_report(db,db.get(StoreProfile,active_store(db,auth)))


@app.post('/products/knowledge/refresh')
async def product_knowledge_refresh(request: Request,auth: OWNER,db: DB):
    store=db.get(StoreProfile,active_store(db,auth))
    if not store:raise HTTPException(404,'Магазин не выбран')
    result=await asyncio.to_thread(refresh_reviewed_knowledge,db,store)
    request.state.activity_details={'event':'product_knowledge_refreshed','changed_products':result['changed_products'],'added_facts':result['added_facts']}
    return result


@app.get('/products/page')
def products_paginated(auth: AUTH, db: DB, q: str = '', limit: int = 60, offset: int = 0):
    limit, offset = min(200, max(1, limit)), max(0, offset)
    store = db.get(StoreProfile, active_store(db, auth))
    scope = product_scope(store)
    query = select(Product).where(scope)
    count_query = select(func.count()).select_from(Product).where(scope)
    if q:
        condition = Product.name.contains(q, autoescape=True) | Product.brand.contains(q, autoescape=True) | Product.category.contains(q, autoescape=True)
        query, count_query = query.where(condition), count_query.where(condition)
    domains={row.key.removeprefix('official_domain:'):row.value for row in db.scalars(select(Setting).where(Setting.key.startswith('official_domain:')))}
    items = [{**public(p), 'official_domain': domains.get(p.id, '')}
             for p in db.scalars(query.order_by(Product.name, Product.id).offset(offset).limit(limit))]
    total = db.scalar(count_query) or 0
    return {'items': items, 'total': total, 'limit': limit, 'offset': offset, 'has_more': offset + len(items) < total}


from .verified_instructions import attach_verified_instructions


class Fact(Payload):
    text: str = Field(min_length=2, max_length=2000)
    source: str = Field(min_length=2, max_length=1000)
    kind: str = Field(default='instruction', pattern='^(instruction|restriction|faq|feature)$')


class OfficialDomain(Payload):
    domain: str = Field(min_length=4, max_length=255)


def official_host(db, product):
    return str(setting(db, f'official_domain:{product.id}', '') or '').lower()


def official_source(db, product, source):
    parsed = urlsplit(source)
    host = (parsed.hostname or '').lower()
    allowed = official_host(db, product)
    return parsed.scheme == 'https' and bool(allowed) and (host == allowed or host.endswith('.' + allowed))


@app.post('/products/{pid}/official-domain')
def product_official_domain(pid: str, body: OfficialDomain, auth: OWNER, db: DB):
    store = db.get(StoreProfile, active_store(db, auth))
    product = db.get(Product, pid)
    if not product_owned(db,store,product):
        raise HTTPException(404, 'Товар не найден')
    raw = body.domain.strip().lower()
    parsed = urlsplit(raw if '://' in raw else 'https://' + raw)
    host = parsed.hostname or ''
    if parsed.scheme != 'https' or not host or host in ('localhost', '127.0.0.1') or '.' not in host or parsed.username or parsed.password or parsed.port or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('Укажите только официальный домен производителя, например example.com')
    set_setting(db, f'official_domain:{pid}', host)
    db.commit()
    return {'domain': host}


@app.post('/products/{pid}/facts')
def product_fact(pid: str, body: Fact, auth: OWNER, db: DB):
    store_id = active_store(db, auth)
    store = db.get(StoreProfile, store_id)
    can_operate_store(auth, store)
    product = db.get(Product, pid)
    if not product_owned(db,store,product):
        raise HTTPException(404)
    if body.source != 'Карточка товара' and not official_source(db, product, body.source):
        raise ValueError('Источник должен быть карточкой товара или страницей на подтверждённом домене производителя')
    product.facts = product.facts + [{**body.model_dump(), 'verified_at': now(), 'verification_status': 'VERIFIED'}]
    db.commit()
    return public(product)


@app.post('/products/{pid}/research')
async def research_product(pid: str, auth: MARKET_OPERATOR, db: DB):
    store_id = active_store(db, auth)
    store = db.get(StoreProfile, store_id)
    can_operate_store(auth, store)
    product = db.get(Product, pid)
    if not product_owned(db,store,product):
        raise HTTPException(404, 'Товар не найден')
    saved = attach_verified_instructions(db, product)
    from .services import trusted_product_facts
    if saved or any(f.get('kind')=='instruction' for f in trusted_product_facts(db,product)):
        db.commit()
        return {'product': public(product), 'found': saved, 'saved_instructions': saved, 'verified_instructions': any(f.get('kind')=='instruction' and f.get('verification_status')=='VERIFIED' for f in product.facts or [])}
    instruction_sources = await web_research.search_instructions(product, db)
    detail_sources = await web_research.search_instructions(product, db, 'характеристики состав комплектация')
    sources = list({item['url']: item for item in instruction_sources + detail_sources}.values())
    if sources:
        remember_web_instruction_sources(product, sources)
        db.commit()
    return {'product': public(product), 'found': len(sources)}


@app.post('/products/{pid}/facts/{fact_index}/verify')
def verify_product_fact(pid: str, fact_index: int, auth: OWNER, db: DB):
    store_id = active_store(db, auth)
    store = db.get(StoreProfile, store_id)
    product = db.get(Product, pid)
    if not product_owned(db,store,product):
        raise HTTPException(404, 'Товар не найден')
    facts = list(product.facts or [])
    if fact_index < 0 or fact_index >= len(facts):
        raise HTTPException(404, 'Источник не найден')
    fact = dict(facts[fact_index])
    if fact.get('verification_status') != 'WEB_UNVERIFIED' or not official_source(db, product, fact.get('source', '')):
        raise ValueError('Подтвердить можно только страницу на указанном официальном домене производителя')
    fact['verification_status'] = 'VERIFIED'
    fact['verified_at'] = now()
    fact['verified_by'] = auth['user_id']
    facts[fact_index] = fact
    product.facts = facts
    db.commit()
    return public(product)


@app.delete('/products/{pid}/facts/{fact_index}')
def product_fact_delete(pid: str, fact_index: int, auth: MARKET_OPERATOR, db: DB):
    store_id = active_store(db, auth)
    store = db.get(StoreProfile, store_id)
    can_operate_store(auth, store)
    product = db.get(Product, pid)
    if not product_owned(db,store,product):
        raise HTTPException(404, 'Товар не найден')
    facts = list(product.facts or [])
    if fact_index < 0 or fact_index >= len(facts):
        raise HTTPException(404, 'Факт не найден')
    removed = facts.pop(fact_index)
    product.facts = facts
    db.commit()
    return {'removed': removed, 'facts': facts}


@app.get('/analytics')
def stats(auth: AUTH, db: DB, days: int = 0, product_id: str = ''):
    return analytics(db, days, product_id, active_store(db, auth))


@app.get('/analytics/marketplaces')
def marketplace_stats(auth: AUTH, db: DB, days: int = 0):
    days = max(0, min(days, 365))
    allowed = auth.get('store_ids') or []
    stores = list(db.scalars(select(StoreProfile).order_by(StoreProfile.created_at)))
    keys = ('reviews', 'reviews_unanswered', 'reviews_negative', 'reviews_answered', 'reviews_drafts',
            'questions', 'questions_unanswered', 'questions_answered', 'questions_drafts')
    totals = dict.fromkeys(keys, 0)
    rows = []
    is_question = Review.marketplace.like('%_question')
    for store in stores:
        filters = [Review.wb_account_id == store.id]
        if days:
            filters.append(Review.created_at >= (datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
        counts = db.execute(select(
            func.sum(case((~is_question, 1), else_=0)),
            func.sum(case(((~is_question) & (Review.is_answered == False), 1), else_=0)),
            func.sum(case(((~is_question) & (Review.rating <= 3), 1), else_=0)),
            func.sum(case(((~is_question) & (Review.is_answered == True), 1), else_=0)),
            func.sum(case(((~is_question) & (Review.is_answered == False) & Draft.id.is_not(None), 1), else_=0)),
            func.sum(case((is_question, 1), else_=0)),
            func.sum(case((is_question & (Review.is_answered == False), 1), else_=0)),
            func.sum(case((is_question & (Review.is_answered == True), 1), else_=0)),
            func.sum(case((is_question & (Review.is_answered == False) & Draft.id.is_not(None), 1), else_=0)),
        ).select_from(Review).outerjoin(Draft, Draft.review_id == Review.id).where(*filters)).one()
        sync = {}
        for kind in ('products', 'reviews', 'questions'):
            latest = db.scalar(select(Job).where(Job.kind == kind, Job.result['account_id'].as_string() == store.id).order_by(Job.created_at.desc()).limit(1))
            sync[kind] = {'status': latest.status, 'started_at': latest.created_at, 'progress': latest.progress,
                          'processed': (latest.result or {}).get('processed'), 'message': (latest.result or {}).get('message', '')} if latest else None
        item = {'id': store.id, 'name': store.name, 'provider': store.provider, 'sync': sync,
                'accessible': True,
                'can_operate': (auth['role'] == 'owner' or '*' in allowed or store.id in allowed) and ('*' in auth.get('permissions', []) or store.provider + ':operate' in auth.get('permissions', [])),
                'connected': bool(db.get(Account, store.id)),
                **{key: int(value or 0) for key, value in zip(keys, counts)}}
        rows.append(item)
        for key in keys:
            totals[key] += item[key]
    trend = []
    if stores:
        since = (datetime.now(timezone.utc) - timedelta(days=13)).date()
        day = func.substr(Review.created_at, 1, 10).label('day')
        trend_rows = db.execute(select(
            day,
            func.sum(case((~is_question, 1), else_=0)),
            func.sum(case((is_question, 1), else_=0)),
            func.sum(case(((~is_question) & (Review.rating <= 3), 1), else_=0)),
        ).where(Review.wb_account_id.in_([store.id for store in stores]), Review.created_at >= since.isoformat())
          .group_by('day')).all()
        by_day = {day: (int(reviews or 0), int(questions or 0), int(negative or 0))
                  for day, reviews, questions, negative in trend_rows}
        trend = [{'date': (since + timedelta(days=offset)).isoformat(),
                  'reviews': by_day.get((since + timedelta(days=offset)).isoformat(), (0, 0, 0))[0],
                  'questions': by_day.get((since + timedelta(days=offset)).isoformat(), (0, 0, 0))[1],
                  'negative': by_day.get((since + timedelta(days=offset)).isoformat(), (0, 0, 0))[2]}
                 for offset in range(14)]
    return {'days': days, 'totals': totals, 'stores': rows, 'trend': trend}


@app.get('/work-queue')
def work_queue(auth: AUTH, db: DB, kind: str = '', store_id: str = '', limit: int = 50, offset: int = 0):
    if kind not in ('', 'reviews', 'questions'):
        raise HTTPException(422, 'Неизвестный тип обращения')
    ids = [s.id for s in db.scalars(select(StoreProfile)) if not store_id or s.id == store_id]
    if not ids:
        return {'items': [], 'total': 0, 'limit': limit, 'offset': offset}
    conditions = [Review.wb_account_id.in_(ids), Review.is_answered == False]
    if kind:
        conditions.append(Review.marketplace.like('%_question') if kind == 'questions' else ~Review.marketplace.like('%_question'))
    limit, offset = min(100, max(1, limit)), max(0, offset)
    total = db.scalar(select(func.count()).select_from(Review).where(*conditions)) or 0
    rows = db.execute(select(Review, Product.name, StoreProfile.name, StoreProfile.provider, Draft.id).join(Product, Product.id == Review.product_id).join(StoreProfile, StoreProfile.id == Review.wb_account_id).outerjoin(Draft, Draft.review_id == Review.id).where(*conditions).order_by(Review.manual.desc(), Review.created_at.desc(), Review.id).offset(offset).limit(limit)).all()
    return {'items': [{'id': review.id, 'store_id': review.wb_account_id, 'store_name': name, 'provider': provider,
                       'kind': 'questions' if review.marketplace.endswith('_question') else 'reviews',
                       'product': product, 'text': review.text, 'rating': review.rating, 'created_at': review.created_at,
                       'manual': review.manual, 'status': review.status, 'has_draft': draft_id is not None}
                      for review, product, name, provider, draft_id in rows],
            'total': total, 'limit': limit, 'offset': offset}


@app.get('/quality-report')
def reply_quality(auth: AUTH, db: DB, days: int = 30):
    return quality_report(db, days, active_store(db, auth))


class DraftInput(Payload):
    instruction: str = Field(default='', max_length=1000)


class SingleDraftJobInput(DraftInput):
    review_id: str = Field(min_length=1, max_length=200)


@app.post('/jobs/single-draft')
async def schedule_single_draft(body: SingleDraftJobInput, background: BackgroundTasks, auth: MARKET_OPERATOR, db: DB):
    if not setting(db, 'assistant_enabled', True):
        raise ValueError('Сначала включите ассистента')
    review = db.get(Review, body.review_id)
    if not review or review.wb_account_id != active_store(db, auth) or review.is_answered or review.status == 'publishing':
        raise HTTPException(404, 'Неотвеченный отзыв или вопрос не найден')
    store = db.get(StoreProfile, review.wb_account_id)
    if not store:
        raise HTTPException(404, 'Магазин не найден')
    can_operate_store(auth, store)
    kind = 'single_question_draft' if review.marketplace.endswith('_question') else 'single_draft'
    for existing in db.scalars(select(Job).where(Job.kind == kind, Job.status.in_(['queued', 'running']))):
        if body.review_id in (existing.result or {}).get('selected_ids', []):
            return public(existing)
    job = Job(id=str(uuid4()), kind=kind, priority=40, max_attempts=1,
              result={'account_id': store.id, 'selected_ids': [body.review_id], 'instruction': body.instruction, 'requested_by': auth['user_id'],
                      'message': 'Подготовка ответа поставлена в очередь'})
    db.add(job)
    db.commit()
    background.add_task(run_job_queue)
    return public(job)


@app.post('/reviews/{rid}/draft')
async def draft(rid: str, body: DraftInput, auth: MARKET_OPERATOR, db: DB):
    review = db.get(Review, rid)
    if not review or review.wb_account_id != active_store(db, auth): raise HTTPException(404, 'Отзыв не найден')
    store = db.get(StoreProfile, review.wb_account_id)
    if not store: raise HTTPException(404, 'Магазин не найден')
    can_operate_store(auth, store)
    result = await generate_draft(db, rid, body.instruction)
    item = db.get(Draft, result['id'])
    item.updated_by = auth['user_id']
    if not item.created_by: item.created_by = auth['user_id']
    db.commit()
    return public(item)


@app.post('/questions/{qid}/draft')
async def question_draft(qid: str, body: DraftInput, auth: MARKET_OPERATOR, db: DB):
    question = db.get(Review, qid)
    if not question or not question.marketplace.endswith('_question') or question.wb_account_id != active_store(db, auth):
        raise HTTPException(404, 'Вопрос не найден')
    can_operate_store(auth, db.get(StoreProfile, question.wb_account_id))
    result = await generate_draft(db, qid, body.instruction)
    item = db.get(Draft, result['id'])
    item.updated_by = auth['user_id']
    if not item.created_by:
        item.created_by = auth['user_id']
    db.commit()
    return public(item)


@app.post('/questions/{qid}/publish')
def question_publish(qid: str, auth: MARKET_OPERATOR, db: DB):
    question = db.get(Review, qid)
    if not question or not question.marketplace.endswith('_question') or question.wb_account_id != active_store(db, auth):
        raise HTTPException(404, 'Вопрос не найден')
    can_operate_store(auth, db.get(StoreProfile, question.wb_account_id))
    return propose_publish(db, [qid])


class QuestionDraftBatch(Payload):
    question_ids: list[str] = Field(default_factory=list, max_length=100)


@app.post('/jobs/question-drafts')
async def schedule_question_drafts(body: QuestionDraftBatch, background: BackgroundTasks, auth: MARKET_OPERATOR, db: DB):
    if not setting(db, 'assistant_enabled', True):
        raise ValueError('Сначала включите ассистента')
    active = active_store(db, auth)
    store = db.get(StoreProfile, active)
    if not store: raise HTTPException(404, 'Магазин не найден')
    can_operate_store(auth, store)
    selected = list(dict.fromkeys(body.question_ids))
    if selected:
        valid = set(db.scalars(select(Review.id).where(Review.id.in_(selected), Review.wb_account_id == active, Review.marketplace.like('%_question'), Review.is_answered == False)))
        if valid != set(selected):
            raise ValueError('В выборке есть недоступные или уже отвеченные вопросы')
    existing = db.scalar(select(Job).where(Job.kind == 'question_drafts', Job.status.in_(['queued','running'])))
    if existing:
        return public(existing)
    job = Job(id=str(uuid4()), kind='question_drafts', priority=50, result={'account_id': active, 'selected_ids': selected, 'message': 'Подготовка ответов на вопросы поставлена в очередь'})
    db.add(job); db.commit(); background.add_task(run_job_queue)
    return public(job)


@app.get('/jobs/question-drafts/preview')
def preview_question_drafts(auth: AUTH, db: DB):
    active = active_store(db, auth)
    total = db.scalar(select(func.count()).select_from(Review).outerjoin(Draft, Draft.review_id == Review.id).where(Review.wb_account_id == active, Review.marketplace.like('%_question'), Review.is_answered == False, Review.status != 'publishing', Draft.id.is_(None))) or 0
    return {'total': total, 'estimated_minutes': max(1, round(total * 25 / 60)) if total else 0}


class DraftEdit(Payload):
    text: str = Field(min_length=2, max_length=5000)
    revision: int


@app.patch('/drafts/{did}')
def draft_edit(did: str, body: DraftEdit, auth: MARKET_OPERATOR, db: DB):
    existing = db.get(Draft, did)
    review = db.get(Review, existing.review_id) if existing else None
    if not review or review.wb_account_id != active_store(db, auth):
        raise HTTPException(404, 'Черновик не найден')
    store = db.get(StoreProfile, review.wb_account_id)
    can_operate_store(auth, store)
    if existing.revision != body.revision:
        raise HTTPException(409, 'Черновик изменён другим пользователем. Сверьте версии перед сохранением')
    result = edit_draft(db, did, body.text, body.revision, auth['user_id'])
    return draft_public(db, db.get(Draft, did))


class DraftFeedback(Payload):
    rating: str = Field(pattern='^(positive|negative)$')
    reasons: list[str] = Field(default_factory=list, max_length=5)


@app.post('/drafts/{did}/feedback')
def draft_feedback(did: str, body: DraftFeedback, auth: MARKET_OPERATOR, db: DB):
    draft = db.get(Draft, did)
    review = db.get(Review, draft.review_id) if draft else None
    if not draft or not review or review.wb_account_id != active_store(db, auth):
        raise HTTPException(404, 'Черновик не найден')
    store = db.get(StoreProfile, review.wb_account_id)
    can_operate_store(auth, store)
    reasons = [reason.strip()[:100] for reason in body.reasons if reason.strip()][:5]
    quality = dict(draft.quality or {})
    quality['owner_feedback'] = {'rating': body.rating, 'reasons': reasons, 'at': now()}
    draft.quality = quality
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
def publish_proposal(body: PublishInput, auth: MARKET_OPERATOR, db: DB):
    active = active_store(db, auth)
    store = db.get(StoreProfile, active)
    can_operate_store(auth, store)
    if any(not (review := db.get(Review, rid)) or review.wb_account_id != active for rid in body.review_ids):
        raise HTTPException(404, 'В выборке есть отзыв другого магазина')
    return propose_publish(db, body.review_ids)


@app.get('/actions')
def actions(auth: AUTH, db: DB):
    active_statuses = ['pending', 'executing', 'needs_review']
    rows = list(db.scalars(select(Action).where(Action.status.in_(active_statuses)).order_by(Action.created_at.desc()).limit(100)))
    if auth['role'] == 'owner':
        return [public(a) for a in rows]
    active = active_store(db, auth)
    return [public(a) for a in rows if a.kind == 'publish' and all((review := db.get(Review, item.get('review_id'))) and review.wb_account_id == active for item in (a.payload or {}).get('items', []))]


@app.post('/publications/reconcile')
async def reconcile_publications(auth: WB_OPERATOR, db: DB):
    active = active_store(db, auth)
    can_operate_store(auth, db.get(StoreProfile, active))
    wb_token(db, active)
    await wb.sync_reviews(db, active)
    checked, confirmed, mismatched = 0, 0, 0
    for publication in db.scalars(select(Publication).where(Publication.status.in_(['uncertain', 'in_flight']))):
        review = db.get(Review, publication.review_id)
        if not review or review.wb_account_id != active:
            continue
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
def remove_action(aid: str, auth: OWNER, db: DB):
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
async def confirmation(aid: str, body: Confirm, request: Request, auth: AUTH, db: DB):
    action = db.get(Action, aid)
    if action and action.kind == 'publish':
        marketplace_operator(auth)
        active = active_store(db, auth)
        store = db.get(StoreProfile, active)
        can_operate_store(auth, store)
        if any(not (review := db.get(Review, item['review_id'])) or review.wb_account_id != active for item in action.payload['items']):
            raise HTTPException(404, 'Действие другого магазина')
    elif action and action.kind == 'memory':
        owner(auth)
    async with action_lock:
        result = await confirm_action(db, aid, body.manual_ack, auth['user_id'])
        if action and action.kind == 'memory' and result.get('status') == 'completed':
            request.state.activity_details = rule_details(action.payload,'rule_added')
        return result


@app.post('/actions/{aid}/cancel')
def cancel(aid: str, request: Request, auth: AUTH, db: DB):
    action = db.get(Action, aid)
    if not action or action.status != 'pending':
        raise ValueError('Действие уже обработано')
    if action.kind == 'publish':
        marketplace_operator(auth)
        active = active_store(db, auth)
        store = db.get(StoreProfile, active)
        can_operate_store(auth, store)
        if any(not (review := db.get(Review, item['review_id'])) or review.wb_account_id != active for item in action.payload['items']):
            raise HTTPException(404, 'Действие другого магазина')
    else:
        owner(auth)
    action.status = 'cancelled'
    if action.kind == 'publish':
        for item in action.payload['items']:
            review = db.get(Review, item['review_id'])
            if review.status == 'pending_confirmation':
                review.status = 'manual_review' if review.manual else 'draft_ready'
    db.commit()
    if action.kind == 'memory':
        request.state.activity_details = rule_details(action.payload,'rule_rejected')
    return {'ok': True}


class MemoryInput(Payload):
    text: str = Field(min_length=2, max_length=2000)
    kind: str = 'GLOBAL_RULE'
    scope: str = 'global'


@app.post('/memory/propose')
def memory_propose(body: MemoryInput, request: Request, auth: AUTH, db: DB):
    action = Action(id=str(uuid4()), kind='memory', payload=body.model_dump())
    db.add(action)
    db.commit()
    request.state.activity_details = rule_details(action.payload,'rule_proposed')
    return public(action)


@app.patch('/actions/{aid}/memory')
def memory_scope(aid: str, body: MemoryInput, request: Request, auth: OWNER, db: DB):
    action = db.get(Action, aid)
    if not action or action.status != 'pending' or action.kind != 'memory':
        raise ValueError('Нет ожидающего предложения памяти')
    action.payload = body.model_dump()
    db.commit()
    request.state.activity_details = rule_details(action.payload,'rule_updated')
    return public(action)


@app.get('/memory')
def memories(auth: AUTH, db: DB):
    return [{**public(m),**setting(db,'memory-meta:'+m.id,{})} for m in db.scalars(select(Memory))]


class MemoryEnabled(Payload):
    enabled: bool


@app.patch('/memory/{mid}/enabled')
def memory_enabled(mid: str, body: MemoryEnabled, request: Request, auth: OWNER, db: DB):
    item=db.get(Memory,mid)
    if not item:raise HTTPException(404,'Правило не найдено')
    item.enabled=body.enabled
    meta=setting(db,'memory-meta:'+mid,{})
    actor=db.get(User,auth['user_id'])
    set_setting(db,'memory-meta:'+mid,{**meta,'updated_at':now(),'updated_by_name':actor.display_name if actor else auth.get('username','')})
    db.commit()
    request.state.activity_details={**rule_details(public(item),'rule_updated'),'event':'rule_enabled' if item.enabled else 'rule_disabled'}
    return {**public(item),**setting(db,'memory-meta:'+mid,{})}


class RulePreviewInput(Payload):
    text: str = Field(min_length=2,max_length=2000)
    example: str = Field(min_length=1,max_length=5000)
    rating: int = Field(ge=1,le=5,default=3)
    product_id: str = ''


@app.post('/memory/preview')
async def memory_preview(body: RulePreviewInput, auth: AUTH, db: DB):
    from .safety import rule_reply,safe_fallback_reply,validate_reply,classify
    from .reply_policy import customer_reply_policy
    from .services import trusted_product_facts,assess_reply
    product=db.get(Product,body.product_id) if body.product_id else Product(id='preview',name='Товар',brand='',category='',facts=[])
    if not product:raise HTTPException(404,'Товар не найден')
    if body.product_id and not product_owned(db,db.get(StoreProfile,active_store(db,auth)),product):raise HTTPException(404,'Товар не найден в выбранном магазине')
    facts=trusted_product_facts(db,product) if body.product_id else []
    review=Review(id='preview',product_id=product.id,rating=body.rating,text=body.example,risk=classify(body.example,product.name+' '+product.category)['risk'])
    reply=rule_reply(review,product,facts=facts)
    note='Предварительный ответ. Правило и ответ не сохраняются.'
    rules=[m.text for m in db.scalars(select(Memory).where(Memory.enabled==True)) if m.scope in ('global','product:'+product.id,'brand:'+product.brand,'category:'+product.category)]+[body.text]
    if review.risk=='NORMAL':
        try:
            reply=await asyncio.wait_for(ollama.chat([{'role':'system','content':customer_reply_policy()+'\nПравила владельца: '+json.dumps(rules,ensure_ascii=False)},{'role':'user','content':json.dumps({'review_data':body.example,'rating':body.rating,'product':product.name,'verified_facts':facts},ensure_ascii=False)}]),timeout=12)
        except Exception:
            reply=reply or safe_fallback_reply(review,product,facts=facts)
            note='Показан ответ по проверенным сценариям. Свободную формулировку правила сейчас не удалось проверить; ограничения на слова применены.'
    for rule in rules:
        if any(x in rule.lower() for x in ('не использ','не пиши')):
            for word in re.findall('[«“\"]([^»”\"]+)[»”\"]',rule):reply=re.sub(re.escape(word),'',reply,flags=re.I)
    try:reply=validate_reply(mask_vin(reply))
    except ValueError:
        reply=safe_fallback_reply(review,product,facts=facts)
        for rule in rules:
            if any(x in rule.lower() for x in ('не использ','не пиши')):
                for word in re.findall('[«“\"]([^»”\"]+)[»”\"]',rule):reply=re.sub(re.escape(word),'',reply,flags=re.I)
        try:reply=validate_reply(mask_vin(reply))
        except ValueError:raise HTTPException(422,'Правило не позволяет составить корректный пример. Уточните ограничения.') from None
        note='Показан безопасный сценарий. Пример с новым правилом требует дополнительной проверки.'
    return {'text':reply,'quality':assess_reply(review,product,reply,facts),'note':note}


@app.delete('/memory/{mid}')
def memory_delete(mid: str, request: Request, auth: OWNER, db: DB):
    existing = db.get(Memory,mid)
    details = rule_details(public(existing),'rule_deleted') if existing else {}
    result = db.execute(delete(Memory).where(Memory.id == mid))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(404, 'Правило уже удалено или не найдено')
    db.commit()
    request.state.activity_details = details
    return {'ok': True, 'id': mid}


class Chat(Payload):
    text: str = Field(min_length=1, max_length=5000)
    conversation_id: str | None = None
    selection: dict[str,str] = Field(default_factory=dict)
    voice_mode: bool = False


chat_lock = asyncio.Lock()


@app.post('/chat')
async def chat(body: Chat, request: Request, auth: AUTH, db: DB):
    if not setting(db, 'assistant_enabled', True):
        raise ValueError('Ассистент отключён владельцем')
    async with chat_lock:
        store_id = active_store(db, auth)
        store = db.get(StoreProfile, store_id)
        selection = {**body.selection, 'active_store_id':store_id}
        assigned = auth['role'] == 'owner' or '*' in (auth.get('store_ids') or []) or store_id in (auth.get('store_ids') or [])
        allowed = assigned and ('*' in auth.get('permissions', []) or bool(store and store.provider + ':operate' in auth.get('permissions', [])))
        result = await assistant.send(db, body.text, body.conversation_id, selection, body.voice_mode, allowed)
        data = result.get('data')
        if isinstance(data,dict) and data.get('kind') == 'memory' and isinstance(data.get('payload'),dict):
            request.state.activity_details = rule_details(data['payload'],'rule_proposed')
        return result


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


@app.post('/voice/wake')
async def voice_wake(auth: AUTH, wake_word: str = Form(...), audio: UploadFile = File(...)):
    word = ''.join(character for character in wake_word.lower().strip() if character.isalnum() or character in (' ', '-'))[:30]
    if not word:
        raise ValueError('Укажите ключевое слово')
    if audio.content_type not in ('audio/wav', 'audio/x-wav', 'application/octet-stream'):
        raise ValueError('Поддерживается запись WAV')
    raw = await audio.read(12 * 1024 * 1024 + 1)
    text_value = await asyncio.to_thread(voice.transcribe, raw, [word, '[unk]'])
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
async def catalog_import(auth: OWNER, db: DB, file: UploadFile = File(...), source: str = Form(...), verified: bool = Form(False)):
    raw = await file.read(10 * 1024 * 1024 + 1)
    try:
        return import_catalog(db, raw, file.filename or '', source, verified)
    except (KeyError, TypeError, UnicodeError, StopIteration):
        raise ValueError('Неверный формат каталога') from None


@app.get('/fitment/catalogs')
def fitment_catalogs(auth: AUTH, db: DB):
    return catalog_summary(db,active_store(db,auth))


@app.post('/fitment/catalogs/import')
async def fitment_catalog_upload(auth: OWNER, db: DB, file: UploadFile=File(...), source: str=Form(...), verified: bool=Form(False), complete: bool=Form(False), mapping: str=Form('{}')):
    source=source.strip()
    if not 2<=len(source)<=1000:raise ValueError('Укажите название или ссылку на источник каталога')
    raw=await file.read(10*1024*1024+1)
    from .workspace_tools import parse_mapping,preview_cross_catalog
    from .fitment_catalog import FIELDS
    columns=parse_mapping(mapping,FIELDS)
    try:
        preview=await asyncio.to_thread(preview_cross_catalog,db,raw,file.filename or '',active_store(db,auth),columns)
        if not preview['can_import']:raise ValueError('Исправьте ошибки в предварительном просмотре перед импортом')
        return await asyncio.to_thread(import_cross_catalog,db,raw,file.filename or '',active_store(db,auth),source,verified,complete,columns)
    except (KeyError,TypeError,UnicodeError,StopIteration,zipfile.BadZipFile,csv.Error):raise ValueError('Не удалось прочитать каталог. Проверьте формат XLSX или CSV') from None


@app.post('/fitment/catalogs/preview')
async def fitment_catalog_preview(auth: OWNER, db: DB, file: UploadFile=File(...), mapping: str=Form('{}')):
    from .workspace_tools import parse_mapping,preview_cross_catalog
    from .fitment_catalog import FIELDS
    raw=await file.read(10*1024*1024+1)
    try:return await asyncio.to_thread(preview_cross_catalog,db,raw,file.filename or '',active_store(db,auth),parse_mapping(mapping,FIELDS))
    except (KeyError,TypeError,UnicodeError,StopIteration,zipfile.BadZipFile,csv.Error):raise ValueError('Не удалось прочитать файл. Используйте XLSX или CSV UTF-8') from None


class CatalogEnabled(Payload):
    active: bool


@app.post('/fitment/catalogs/{catalog_id}/enabled')
def fitment_catalog_enabled(catalog_id: str, body: CatalogEnabled, auth: OWNER, db: DB):
    item=db.get(FitmentImport,catalog_id)
    if not item or item.store_id!=active_store(db,auth):raise HTTPException(404,'Каталог не найден')
    item.active=body.active;db.commit()
    return {'ok':True}


@app.get('/fitment/template')
def fitment_template(auth: AUTH):
    return Response('\ufeffbrand;part_number;wb_nm_id;oe_number;oe_brand;category;make;model;year_from;year_to;body;engine_code;vin;fit;stock;cross_brand;cross_number\r\n',media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="cross-catalog-template.csv"'})


@app.get('/fitment/questions')
def fitment_pending_questions(auth: AUTH, db: DB):
    questions=list(db.scalars(select(Review).where(Review.wb_account_id==active_store(db,auth),Review.marketplace.like('%_question'),Review.is_answered==False).order_by(Review.created_at.desc()).limit(200)))
    return [{'id':q.id,'text':q.text,'product_id':q.product_id,'vin':next(iter(vehicle_identifiers(q.text)),''),'oe_number':next(iter(oem_numbers(q.text)),'') if len(oem_numbers(q.text))==1 else '', 'name':db.get(Product,q.product_id).name} for q in questions if question_intent(q.text)=='совместимость'][:50]


@app.get('/fitment/internet/settings')
def internet_fitment_settings(auth: AUTH,db: DB):
    return {'enabled':bool(setting(db,'internet-fitment-enabled:'+active_store(db,auth),False)),'providers':['PartSouq — публичный каталог OEM','NHTSA vPIC — сведения об автомобиле'],'timeout_seconds':18}


class InternetFitmentSettings(Payload):
    enabled: bool


@app.post('/fitment/internet/settings')
def internet_fitment_settings_update(body: InternetFitmentSettings,auth: OWNER,db: DB):
    set_setting(db,'internet-fitment-enabled:'+active_store(db,auth),body.enabled);db.commit()
    return {'enabled':body.enabled}


def fitment_research_question(qid,auth,db):
    q=db.get(Review,qid)
    if not q or q.wb_account_id!=active_store(db,auth) or not q.marketplace.endswith('_question'):raise HTTPException(404,'Вопрос не найден')
    return q,db.get(Product,q.product_id)


@app.get('/fitment/questions/{qid}/research')
def fitment_last_research(qid: str,auth: AUTH,db: DB):
    from .internet_fitment import last_research
    q,p=fitment_research_question(qid,auth,db)
    return {**last_research(db,q,p),'saved_fitment':fitment_answer(db,q,p),'product':{'id':p.id,'brand':p.brand,'part_number':p.part_number,'name':p.name},'can_confirm':auth['role']=='owner'}


class FitmentResearchInput(Payload):
    refresh: bool=False


@app.post('/fitment/questions/{qid}/research')
async def fitment_research(qid: str,body: FitmentResearchInput,auth: MARKET_OPERATOR,db: DB):
    from .internet_fitment import research_question
    q,p=fitment_research_question(qid,auth,db)
    can_operate_store(auth,db.get(StoreProfile,q.wb_account_id))
    if not setting(db,'internet-fitment-enabled:'+q.wb_account_id,False):raise ValueError('Владелец может включить интернет-подбор в разделе Товары')
    return await research_question(db,q,p,body.refresh)


class FitmentEvidenceInput(Payload):
    brand: str=Field(min_length=1,max_length=100)
    part_number: str=Field(min_length=1,max_length=100)
    wb_nm_id: str=Field(default='',max_length=12)
    source: str=Field(min_length=8,max_length=1000)
    fit: bool=True
    oe_brand: str=Field(default='',max_length=100)
    oe_number: str=Field(default='',max_length=40)
    remember_cross: bool=False


@app.post('/fitment/questions/{question_id}/evidence')
def fitment_question_evidence(question_id: str, body: FitmentEvidenceInput, auth: OWNER, db: DB):
    store=active_store(db,auth);q=db.get(Review,question_id)
    if not q or q.wb_account_id!=store or not q.marketplace.endswith('_question'):raise HTTPException(404,'Вопрос не найден')
    vins=set(vehicle_identifiers(q.text))
    oems=oem_numbers(q.text)
    if len(vins)>1 or len(oems)>1 or (not vins and not oems):raise ValueError('Для подтверждения нужен один полный VIN, Frame или оригинальный OEM-номер в вопросе покупателя')
    p=db.get(Product,q.product_id)
    host=(urlsplit(body.source).hostname or '').lower();official=str(setting(db,'official_domain:'+p.id,'')).lower()
    if not body.source.startswith('https://') or not (host in ('uniqom.ru','partsouq.com') or host.endswith('.uniqom.ru') or (official and (host==official or host.endswith('.'+official)))):raise ValueError('Используйте ссылку UNIQOM, PartSouq или официальный домен производителя из карточки товара')
    if body.wb_nm_id and not re.fullmatch(r'[1-9][0-9]{0,11}',body.wb_nm_id):raise ValueError('Неверный артикул Wildberries')
    if body.wb_nm_id:
        linked=db.get(Product,body.wb_nm_id)
        listed=body.wb_nm_id==q.product_id or db.get(StoreListing,(store,body.wb_nm_id))
        from .fitment_catalog import norm
        if not linked or linked.marketplace!='wb' or not listed:raise ValueError('Нужен артикул WB из ассортимента этого магазина')
        if linked.brand and norm(linked.brand)!=norm(body.brand):raise ValueError('Бренд не совпадает с карточкой WB')
        if linked.part_number and norm(linked.part_number)!=norm(body.part_number):raise ValueError('Артикул производителя не совпадает с карточкой WB')
    result=save_question_evidence(db,q,p,body.model_dump())
    return {'ok':True,**result,'message':'Результат подбора сохранён. Подготовьте новый вариант ответа на вопрос.'}


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
def brand_save(body: BrandInput, auth: OWNER, db: DB):
    db.merge(BrandPreference(**body.model_dump()))
    db.commit()
    return {'ok': True}


@app.post('/backup')
def backup_create(auth: OWNER):
    return {'file': backup(), 'secrets_included': False}


class PortablePassword(Payload):
    password: str = Field(min_length=12, max_length=1024)


portable_lock = asyncio.Lock()


class PortableSyncInput(Payload):
    enabled: bool = False
    directory: str = Field(default='', max_length=1000)
    password: str = Field(default='', max_length=1024)
    hours: int = Field(default=24, ge=1, le=720)


def portable_sync_public(db):
    value = setting(db, 'portable_sync', {'enabled':False,'directory':'','hours':24})
    return {**value,'configured':bool(db.get(Account,'portable:sync'))}


@app.get('/settings/portable-sync')
def portable_sync_get(auth: AUTH, db: DB):
    return portable_sync_public(db)


@app.patch('/settings/portable-sync')
def portable_sync_set(body: PortableSyncInput, auth: OWNER, db: DB):
    destination = Path(body.directory).expanduser().resolve() if body.directory else None
    if body.enabled and (not destination or not destination.is_dir()):
        raise ValueError('Выберите существующую папку Google Drive, OneDrive или другую папку синхронизации')
    account = db.get(Account,'portable:sync')
    if body.password:
        if len(body.password) < 12:
            raise ValueError('Пароль синхронизации должен содержать не менее 12 символов')
        db.merge(Account(id='portable:sync',encrypted_token=encrypt_secret(body.password)))
        account = True
    if body.enabled and not account:
        raise ValueError('Задайте отдельный пароль зашифрованной синхронизации')
    current = setting(db,'portable_sync',{})
    current.update(enabled=body.enabled,directory=str(destination) if destination else '',hours=body.hours)
    set_setting(db,'portable_sync',current);db.commit()
    return portable_sync_public(db)


@app.post('/portable/sync-now')
async def portable_sync_now(auth: OWNER, db: DB):
    config=setting(db,'portable_sync',{})
    account=db.get(Account,'portable:sync')
    destination=Path(str(config.get('directory',''))).expanduser().resolve()
    if not config.get('enabled') or not account or not destination.is_dir():
        raise ValueError('Сначала настройте зашифрованную синхронизацию')
    async with portable_lock:
        password=decrypt_secret(account.encrypted_token)
        export=await asyncio.to_thread(portable_backup,password)
        copied=await asyncio.to_thread(shutil.copy2,export,destination/export.name)
        verified=await asyncio.to_thread(portable_verify,copied,password)
    config.update(last_sync=time.time(),last_verified=time.time(),last_verified_counts={'reviews':verified['reviews'],'drafts':verified['drafts']},last_file=Path(copied).name,last_error='')
    set_setting(db,'portable_sync',config);db.commit()
    return {'ok':True,'file':Path(copied).name}


@app.post('/portable/verify')
async def portable_sync_verify(auth: OWNER, db: DB):
    config = setting(db, 'portable_sync', {})
    account = db.get(Account, 'portable:sync')
    directory = Path(str(config.get('directory', ''))).expanduser().resolve()
    filename = str(config.get('last_file', ''))
    if not account or not directory.is_dir() or not filename or Path(filename).name != filename or not filename.endswith('.wbai'):
        raise ValueError('Сначала создайте зашифрованную копию')
    source = (directory / filename).resolve()
    if source.parent != directory or not source.is_file():
        raise ValueError('Последняя зашифрованная копия не найдена')
    async with portable_lock:
        result = await asyncio.to_thread(portable_verify, source, decrypt_secret(account.encrypted_token))
    return {**result, 'file': filename}


@app.post('/portable/export')
async def portable_export(body: PortablePassword, auth: OWNER):
    path = await asyncio.to_thread(portable_backup, body.password)
    return FileResponse(path, media_type='application/octet-stream', filename=path.name, headers={'X-Backup-Encrypted':'AES-256-GCM','X-Secrets-Included':'false'})


@app.post('/portable/restore')
async def portable_import(auth: OWNER, password: str = Form(...), file: UploadFile = File(...)):
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
