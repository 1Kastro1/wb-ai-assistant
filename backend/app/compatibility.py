"""Conservative, evidence-only fitment. Unknown restrictions never imply fit."""
import csv
import io
import json
import re
import hashlib
from uuid import uuid4
from sqlalchemy import select
from .db import CatalogSource, CatalogPart, CatalogImport, OE, Cross, Application, Product, BrandPreference, CompatibilityCheck, public, now


def normalize_oe(value):
    return re.sub(r'[^A-Z0-9]', '', str(value).upper())


def validate_vin(value):
    value = value.strip().upper()
    if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', value):
        raise ValueError('VIN должен содержать 17 символов без I, O и Q')
    return value


FIELDS = ('make', 'model', 'generation', 'engine', 'engine_code', 'axle', 'brake_system', 'disc_diameter', 'disc_thickness', 'pr_code', 'market', 'body', 'drive')
REQUIRED = ('make', 'model', 'generation', 'engine_code', 'year', 'axle')


def evaluate(vehicle, application, verified):
    missing = [key for key in REQUIRED if not vehicle.get(key)]
    if missing or not vehicle.get('verified') or not verified:
        return 'INSUFFICIENT_DATA', missing or ['Подтвердите источник автомобиля и каталога']
    # Incomplete generic catalog entries cannot establish exact fitment.
    if any(not application.get(k) for k in ('make', 'model', 'generation', 'engine_code', 'year_from', 'year_to', 'axle')):
        return 'INSUFFICIENT_DATA', ['Неполная запись применяемости']
    conditions = []
    for key in FIELDS:
        expected = application.get(key)
        if expected in ('', None):
            continue
        if vehicle.get(key) in ('', None):
            conditions.append(f'{key}: {expected}')
        elif str(vehicle[key]).strip().casefold() != str(expected).strip().casefold():
            return 'VERIFIED_NOT_FIT', [f'{key}: требуется {expected}']
    if not int(application['year_from']) <= int(vehicle['year']) <= int(application['year_to']):
        return 'VERIFIED_NOT_FIT', ['Год вне диапазона применяемости']
    for key in ('notes', 'vin_range'):
        if application.get(key):
            conditions.append(str(application[key]))
    for bound, op in (('production_date_from', 'min'), ('production_date_to', 'max')):
        if application.get(bound):
            date = vehicle.get('production_date')
            if not date:
                conditions.append(f'{bound}: {application[bound]}')
            elif (op == 'min' and date < application[bound]) or (op == 'max' and date > application[bound]):
                return 'VERIFIED_NOT_FIT', ['Дата производства вне диапазона']
    return ('CONDITIONAL_FIT', conditions) if conditions else ('VERIFIED_FIT', [])


def import_catalog(db, raw, filename, source_name, verified=False):
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError('Максимальный размер каталога — 10 МБ')
    digest = hashlib.sha256(raw + source_name.encode() + str(verified).encode()).hexdigest()
    old = db.scalar(select(CatalogImport).where(CatalogImport.digest == digest))
    if old:
        return {'rows': old.rows, 'duplicate': True}
    extension = filename.rsplit('.', 1)[-1].lower()
    if extension == 'json':
        rows = json.loads(raw.decode('utf-8-sig'))
    elif extension == 'csv':
        text = raw.decode('utf-8-sig')
        delimiter = ';' if text.splitlines()[0].count(';') > text.splitlines()[0].count(',') else ','
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    elif extension == 'xlsx':
        import zipfile
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(x.file_size for x in archive.infolist()) > 50 * 1024 * 1024:
                raise ValueError('Слишком большой распакованный XLSX')
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        sheet = iter(workbook.active.values)
        headers = next(sheet)
        rows = [dict(zip(headers, row)) for row in sheet]
        workbook.close()
    else:
        raise ValueError('Поддерживаются CSV, JSON и XLSX')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise ValueError('Каталог должен содержать от 1 до 10000 строк')
    clean = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not all(row.get(k) for k in ('brand', 'part_number')):
            raise ValueError(f'Строка {i}: нужны brand и part_number')
        row = {str(k): v for k, v in row.items() if k is not None and v is not None}
        for key in ('year_from', 'year_to'):
            if row.get(key):
                row[key] = int(row[key])
                if not 1886 <= row[key] <= 2100:
                    raise ValueError(f'Строка {i}: неверный год')
        if row.get('year_from', 0) > row.get('year_to', 2100):
            raise ValueError(f'Строка {i}: неверный диапазон годов')
        clean.append(row)
    sid = str(uuid4())
    db.add(CatalogSource(id=sid, name=source_name, verified=verified))
    db.flush()
    parts = {}
    for row in clean:
        key = (row['brand'], row['part_number'])
        if key not in parts:
            part = CatalogPart(id=str(uuid4()), source_id=sid, brand=str(row['brand']), part_number=str(row['part_number']), product_id=str(row.get('wb_nm_id', '')), category=str(row.get('category', '')))
            db.add(part)
            db.flush()
            parts[key] = part
        part = parts[key]
        for cls, field in ((OE, 'oe_number'), (Cross, 'cross_number')):
            for number in str(row.get(field, '')).split('|'):
                if number.strip():
                    db.add(cls(part_id=part.id, number=normalize_oe(number)))
        db.add(Application(part_id=part.id, data=row))
    db.add(CatalogImport(id=str(uuid4()), source_id=sid, rows=len(clean), digest=digest))
    db.commit()
    return {'rows': len(clean), 'duplicate': False, 'source_id': sid}


def check_compatibility(db, vehicle, part_number='', oe='', same_brand='', category=''):
    parts = db.scalars(select(CatalogPart)).all()
    groups = {}
    for part in parts:
        if part_number and normalize_oe(part.part_number) != normalize_oe(part_number):
            continue
        if category and category.casefold() not in part.category.casefold():
            continue
        oes = [x.number for x in db.scalars(select(OE).where(OE.part_id == part.id))]
        if oe and normalize_oe(oe) not in oes:
            continue
        source = db.get(CatalogSource, part.source_id)
        applications = db.scalars(select(Application).where(Application.part_id == part.id)).all()
        outcomes = [evaluate(vehicle, app.data, source.verified) for app in applications]
        # Applications in ONE source are alternatives (OR). Sources may conflict.
        status = 'INSUFFICIENT_DATA'
        for candidate in ('VERIFIED_FIT', 'CONDITIONAL_FIT', 'INSUFFICIENT_DATA', 'VERIFIED_NOT_FIT'):
            if any(x[0] == candidate for x in outcomes):
                status = candidate
                break
        record = {'part_number': part.part_number, 'brand': part.brand, 'product_id': part.product_id, 'oe': oes, 'source': source.name, 'verified': source.verified, 'status': status, 'conditions': [c for s, cs in outcomes if s == status for c in cs], 'applications': [public(a) for a in applications]}
        groups.setdefault((part.brand, normalize_oe(part.part_number)), []).append(record)
    results = []
    for records in groups.values():
        statuses = {r['status'] for r in records if r['verified']}
        trusted = [r for r in records if r['verified']]
        record = dict(next((r for r in trusted if r['status'] == 'VERIFIED_FIT'), (trusted or records)[0]))
        record['evidence'] = records
        if 'VERIFIED_NOT_FIT' in statuses and statuses & {'VERIFIED_FIT', 'CONDITIONAL_FIT'}:
            record['status'] = 'CATALOG_CONFLICT'
        elif 'CONDITIONAL_FIT' in statuses:
            record['status'] = 'CONDITIONAL_FIT'
        elif 'INSUFFICIENT_DATA' in statuses:
            record['status'] = 'INSUFFICIENT_DATA'
        elif 'VERIFIED_FIT' in statuses:
            record['status'] = 'VERIFIED_FIT'
        if len({r['product_id'] for r in trusted if r['product_id']}) > 1:
            record['status'] = 'CATALOG_CONFLICT'
        record['conditions'] = list(dict.fromkeys(c for r in records for c in r['conditions']))
        preference = db.get(BrandPreference, record['brand'])
        in_stock = bool(record['product_id'] and db.get(Product, record['product_id']))
        allowed = record['brand'] == same_brand or (preference and preference.enabled and (not preference.category or preference.category == category))
        record['recommendable'] = bool(record['status'] == 'VERIFIED_FIT' and in_stock and allowed)
        record['priority'] = 0 if record['brand'] == same_brand else preference.priority if preference else 999
        results.append(record)
    results.sort(key=lambda r: (not r['recommendable'], r['priority'], r['brand']))
    result = {'status': 'CHECKED' if results else 'INSUFFICIENT_DATA', 'vehicle': vehicle, 'items': results, 'checked_at': now()}
    db.add(CompatibilityCheck(id=str(uuid4()), result=result))
    db.commit()
    return result
