from datetime import datetime, timezone
from sqlalchemy import create_engine, event, String, Text, Integer, Boolean, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import DATABASE


def now():
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


engine = create_engine('sqlite:///' + str(DATABASE), connect_args={'check_same_thread': False, 'timeout': 30})


@event.listens_for(engine, 'connect')
def pragmas(connection, _):
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA busy_timeout=30000')


Session = sessionmaker(engine, expire_on_commit=False)


class Setting(Base):
    __tablename__ = 'app_settings'
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class Account(Base):
    __tablename__ = 'wb_accounts'
    id: Mapped[str] = mapped_column(String, primary_key=True, default='owner')
    encrypted_token: Mapped[str] = mapped_column(Text)


class StoreProfile(Base):
    __tablename__ = 'store_profiles'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now)


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String, default='member')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String, default=now)


class LoginSession(Base):
    __tablename__ = 'sessions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    expires: Mapped[int] = mapped_column(Integer)
    csrf: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(String, default='owner')


class Product(Base):
    __tablename__ = 'products'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    brand: Mapped[str] = mapped_column(String, default='')
    category: Mapped[str] = mapped_column(String, default='')
    part_number: Mapped[str] = mapped_column(String, default='')
    facts: Mapped[list] = mapped_column(JSON, default=list)


class Review(Base):
    __tablename__ = 'reviews'
    __table_args__ = (UniqueConstraint('wb_account_id', 'wb_review_id'),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    wb_account_id: Mapped[str] = mapped_column(String, default='owner')
    wb_review_id: Mapped[str] = mapped_column(String)
    product_id: Mapped[str] = mapped_column(ForeignKey('products.id'))
    rating: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, default=now)
    is_answered: Mapped[bool] = mapped_column(Boolean, default=False)
    existing_answer: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String, default='unanswered')
    risk: Mapped[str] = mapped_column(String, default='NORMAL')
    manual: Mapped[bool] = mapped_column(Boolean, default=False)
    safety_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    topics: Mapped[list] = mapped_column(JSON, default=list)


class Draft(Base):
    __tablename__ = 'generated_answers'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    review_id: Mapped[str] = mapped_column(ForeignKey('reviews.id'), unique=True)
    text: Mapped[str] = mapped_column(Text)
    original: Mapped[str] = mapped_column(Text)
    edits: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    model: Mapped[str] = mapped_column(String, default='rules-v1')
    prompt_version: Mapped[str] = mapped_column(String, default='1.0')
    quality: Mapped[dict] = mapped_column(JSON, default=dict)


class Action(Base):
    __tablename__ = 'actions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default='pending')
    created_at: Mapped[str] = mapped_column(String, default=now)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class Publication(Base):
    __tablename__ = 'publication_jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    review_id: Mapped[str] = mapped_column(ForeignKey('reviews.id'), unique=True)
    action_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)


class Memory(Base):
    __tablename__ = 'memories'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String, default='GLOBAL_RULE')
    scope: Mapped[str] = mapped_column(String, default='global')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, default='Новый разговор')
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default='')


class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'))
    role: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Audit(Base):
    __tablename__ = 'network_audit'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(String, default=now)
    provider: Mapped[str] = mapped_column(String)
    host: Mapped[str] = mapped_column(String)
    operation: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class ToolCall(Base):
    __tablename__ = 'tool_calls'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    timestamp: Mapped[str] = mapped_column(String, default=now)


class CatalogSource(Base):
    __tablename__ = 'catalog_sources'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class CatalogPart(Base):
    __tablename__ = 'catalog_parts'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey('catalog_sources.id'))
    brand: Mapped[str] = mapped_column(String)
    part_number: Mapped[str] = mapped_column(String)
    product_id: Mapped[str] = mapped_column(String, default='')
    category: Mapped[str] = mapped_column(String, default='')


class OE(Base):
    __tablename__ = 'part_oe_numbers'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    part_id: Mapped[str] = mapped_column(ForeignKey('catalog_parts.id'))
    number: Mapped[str] = mapped_column(String)


class Cross(Base):
    __tablename__ = 'part_crosses'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    part_id: Mapped[str] = mapped_column(ForeignKey('catalog_parts.id'))
    number: Mapped[str] = mapped_column(String)


class Application(Base):
    __tablename__ = 'part_applications'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    part_id: Mapped[str] = mapped_column(ForeignKey('catalog_parts.id'))
    data: Mapped[dict] = mapped_column(JSON)
    verified_at: Mapped[str] = mapped_column(String, default=now)


class CatalogImport(Base):
    __tablename__ = 'catalog_imports'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(String)
    rows: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Vehicle(Base):
    __tablename__ = 'vehicles'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    profile: Mapped[dict] = mapped_column(JSON)


class VinSession(Base):
    __tablename__ = 'vin_sessions'
    vin_hash: Mapped[str] = mapped_column(String, primary_key=True)
    vin_mask: Mapped[str] = mapped_column(String)
    vehicle_id: Mapped[str] = mapped_column(ForeignKey('vehicles.id'))


class BrandPreference(Base):
    __tablename__ = 'brand_preferences'
    brand: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str] = mapped_column(String, default='')
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class CompatibilityCheck(Base):
    __tablename__ = 'compatibility_checks'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='queued')
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=now)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)


def setting(db, key, default=None):
    row = db.get(Setting, key)
    return row.value if row else default


def set_setting(db, key, value):
    db.merge(Setting(key=key, value=value))


def public(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}
