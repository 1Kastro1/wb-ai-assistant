"""Conservative rule identity and transaction-wide protection for all UI writes."""
import unicodedata
from contextlib import contextmanager
from sqlalchemy import select, text
from fastapi import HTTPException
from .db import Memory, IS_SQLITE, setting


def normalized_text(value):
    # Keep punctuation, numbers and negations: similar wording is not proof of
    # equivalent instructions. Never persist the normalized form as policy text.
    return ' '.join(unicodedata.normalize('NFC', value or '').split()).casefold()


def normalized_scope(value):
    kind, separator, target = (value or 'global').partition(':')
    return kind.strip().casefold() + (':' + target.strip().casefold() if separator else '')


def identity(rule):
    return rule.kind or 'GLOBAL_RULE', normalized_scope(rule.scope), normalized_text(rule.text)


def lock_rules(db, *, begin=False):
    """Serialize saves across API processes; release on commit/rollback.

    Call before loading/changing rules. Confirmation already starts an immediate
    SQLite transaction; other routes discard their read-only auth transaction.
    """
    if IS_SQLITE:
        if begin:
            db.rollback()
            db.execute(text('BEGIN IMMEDIATE'))
    else:
        db.execute(text('SELECT pg_advisory_xact_lock(73124, 1)'))


@contextmanager
def rule_write(db, *, begin=True):
    try:
        lock_rules(db, begin=begin)
        yield
    finally:
        # No-op/rejected saves must also release the SQLite writer lock before
        # the activity middleware writes its own independent transaction.
        db.rollback()


def duplicate_rule(db, candidate, *, exclude=''):
    key = identity(candidate)
    for row in db.scalars(select(Memory).order_by(Memory.id)):
        if row.id == exclude or identity(row) != key:
            continue
        # Retired copies remain recoverable history, not competing canonical
        # entries. Enabling a copy still finds its canonical rule below.
        meta = setting(db, 'memory-meta:' + row.id, {})
        if meta.get('duplicate_of'):
            target = db.get(Memory, meta['duplicate_of'])
            if meta.get('duplicate_reason') == 'covered' and target and target.kind == candidate.kind:
                return target
            continue
        return row
    return None


def require_unique(db, candidate, *, exclude=''):
    if exclude:
        target_id = setting(db, 'memory-meta:' + exclude, {}).get('duplicate_of')
        target = db.get(Memory, target_id) if target_id else None
        if target:
            raise HTTPException(409, 'Это правило объединено с существующим. Используйте сохранённое основное правило вместо включения копии.')
    existing = duplicate_rule(db, candidate, exclude=exclude)
    if existing:
        state = 'включено' if existing.enabled else 'отключено — включите его вместо создания копии'
        raise HTTPException(409, 'Такое правило уже существует в этом разделе и области (' + state + '). Дубль не сохранён.')
