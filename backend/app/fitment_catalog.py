"""Short fitment answers require exact, store-scoped catalog evidence."""
import csv
import hashlib
import io
import json
import re
import secrets
import zipfile
from uuid import uuid4
from sqlalchemy import select
from .db import FitmentImport, StoreListing, Product, Review, setting, set_setting, now

FIELDS = ('brand','part_number','wb_nm_id','oe_number','category','make','model','year_from','year_to','body','engine_code','vin','fit','stock','cross_brand','cross_number','engine','drive','axle','position','pr_code','market','generation','production_date_from','production_date_to','notes','vin_range')
FIELDS += ('oe_brand',)
ALIASES = {'бренд':'brand','артикул производителя':'part_number','артикул детали':'part_number','артикул wb':'wb_nm_id','артикул wildberries':'wb_nm_id','код wb':'wb_nm_id','oem':'oe_number','oe':'oe_number','категория':'category','марка':'make','модель':'model','год с':'year_from','год по':'year_to','кузов':'body','двигатель':'engine_code','подходит':'fit','остаток':'stock','бренд аналога':'cross_brand','артикул аналога':'cross_number'}
from .vehicle_identifiers import VIN, FRAME, vehicle_identifiers, valid_vehicle_identifier, identifier_key
ALIASES.update({'артикул':'part_number','производитель':'brand','марка автомобиля':'make','модель автомобиля':'model','ограничения':'notes','примечание':'notes','ось':'axle','сторона':'position','привод':'drive','код двигателя':'engine_code','pr код':'pr_code','part number':'part_number','article':'part_number','wb id':'wb_nm_id'})
ALIASES.update({'frame':'vin','фрейм':'vin','номер кузова':'vin','vin/frame':'vin'})
ALIASES.update({'оригинальный номер':'oe_number','номер oem':'oe_number','номер оем':'oe_number','бренд oem':'oe_brand','марка oem':'oe_brand'})


def oem_numbers(text):
    # Require an explicit OEM/replacement request, never interpret a VIN, year or WB ID as OEM.
    value=re.sub(r'(?<=[A-Z0-9])\s*-\s*(?=[A-Z0-9])','-',text or '',flags=re.I)
    marker=r'(?<![a-zа-яё])(?:oem|oe|оем|оригинальн(?:ый|ого)\s+(?:номер|артикул)|вместо|аналог(?:\s+для)?|замена(?:\s+для)?)\s*(?:№|номер|код|:|#)?\s*'
    numbers=[]
    for match in re.finditer(marker+r'((?=[a-z0-9./-]*[0-9])[a-z0-9]+(?:[-./][a-z0-9]+)*)',value,re.I):
        number=norm(match.group(1))
        if 5<=len(number)<=24 and any(c.isdigit() for c in number) and not VIN.fullmatch(number) and not vehicle_identifiers(match.group(1)):
            numbers.append(number)
    bare=re.fullmatch(r'\s*(?:здравствуйте[!,. ]*)?([a-z0-9]+(?:[-./][a-z0-9]+)*)[?.! ]*',value,re.I)
    if bare:
        code=bare.group(1);number=norm(code)
        if 5<=len(number)<=24 and any(c.isdigit() for c in code) and (any(c.isalpha() for c in code) or '-' in code) and not VIN.fullmatch(number) and not vehicle_identifiers(code):numbers.append(number)
    return set(numbers)


def oe_numbers(row):
    return {norm(value) for value in re.split(r'[;,|\n]+',row.get('oe_number','')) if norm(value)}


def unrestricted(row):
    return not any(row.get(k) for k in ('vin_hash','make','model','body','year_from','year_to','engine_code','engine','drive','axle','position','pr_code','market','generation','production_date_from','production_date_to','notes','vin_range'))

def norm(value):
    return re.sub(r'[^a-zа-яё0-9]','',str(value or '').casefold())

def vin_key(db,store,value):
    key='fitment-salt:'+store
    salt=setting(db,key,'')
    if not salt:
        salt=secrets.token_hex(32);set_setting(db,key,salt)
    return hashlib.sha256((salt+identifier_key(value)).encode()).hexdigest()

def parse_rows(raw,filename):
    if len(raw)>10*1024*1024:raise ValueError('Максимальный размер файла — 10 МБ')
    extension=filename.rsplit('.',1)[-1].lower()
    if extension=='xlsx':
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(f.file_size for f in archive.infolist())>50*1024*1024:raise ValueError('Слишком большой распакованный Excel')
        from openpyxl import load_workbook
        workbook=load_workbook(io.BytesIO(raw),read_only=True,data_only=False,keep_links=False)
        try:
            sheet=iter(workbook.active.values);headers=next(sheet)
            rows=[]
            for row in sheet:
                if len(rows)>=10000:raise ValueError('Не более 10000 строк в одном файле')
                if any(v not in (None,'') for v in row):rows.append(dict(zip(headers,row)))
        finally:workbook.close()
    elif extension=='csv':
        value=raw.decode('utf-8-sig');delimiter=';' if value.splitlines()[0].count(';')>value.splitlines()[0].count(',') else ','
        rows=list(csv.DictReader(io.StringIO(value),delimiter=delimiter))
    else:raise ValueError('Загрузите XLSX или CSV. Старый XLS сохраните как XLSX')
    if not rows or len(rows)>10000:raise ValueError('Нужно от 1 до 10000 строк')
    cleaned=[]
    for i,row in enumerate(rows,2):
        item={}
        for header,value in row.items():
            key=ALIASES.get(str(header or '').strip().casefold(),str(header or '').strip().casefold())
            if key not in FIELDS or value in (None,''):continue
            if isinstance(value,str) and value.startswith('='):raise ValueError(f'Строка {i}: замените формулы значениями')
            if key in ('part_number','oe_number','cross_number','vin') and not isinstance(value,str):raise ValueError(f'Строка {i}: артикулы и VIN должны быть текстом, чтобы сохранить ведущие нули')
            if key=='vin' and key in item and identifier_key(item[key])!=identifier_key(str(value).strip()):raise ValueError(f'Строка {i}: укажите только один VIN или Frame')
            item[key]=str(value).strip()
        if not item.get('brand') or not item.get('part_number'):raise ValueError(f'Строка {i}: нужны бренд и артикул производителя')
        for key in ('year_from','year_to','stock'):
            if key in item:
                try:item[key]=int(item[key])
                except (ValueError,TypeError):raise ValueError(f'Строка {i}: неверное число в {key}') from None
                if item[key]<0 or (key!='stock' and not 1886<=item[key]<=2100):raise ValueError(f'Строка {i}: неверное значение {key}')
        if item.get('year_from',0)>item.get('year_to',2100):raise ValueError(f'Строка {i}: неверный диапазон годов')
        if item.get('wb_nm_id') and not re.fullmatch(r'[1-9][0-9]{0,11}',item['wb_nm_id']):raise ValueError(f'Строка {i}: неверный артикул Wildberries')
        if item.get('vin') and not valid_vehicle_identifier(item['vin']):raise ValueError(f'Строка {i}: укажите полный VIN (17 символов) или Frame, например AGH30-0430241')
        if item.get('fit','да').casefold() not in ('да','нет','true','false','1','0','yes','no'):raise ValueError(f'Строка {i}: подходит — да или нет')
        item['fit']=item.get('fit','да').casefold() in ('да','true','1','yes')
        cleaned.append(item)
    return cleaned

def import_cross_catalog(db,raw,filename,store,source,verified=False,complete=False):
    rows=parse_rows(raw,filename)
    digest=hashlib.sha256(raw+store.encode()+source.encode()+str((verified,complete)).encode()).hexdigest()
    old=db.scalar(select(FitmentImport).where(FitmentImport.store_id==store,FitmentImport.digest==digest,FitmentImport.active==True))
    if old:return {'id':old.id,'rows':len(old.rows),'duplicate':True}
    for i,row in enumerate(rows,2):
        if row.get('wb_nm_id'):
            product=db.get(Product,row['wb_nm_id'])
            if not product or product.marketplace!='wb':raise ValueError(f'Строка {i}: артикул WB не найден среди загруженных товаров')
            if product.brand and norm(product.brand)!=norm(row['brand']):raise ValueError(f'Строка {i}: бренд не совпадает с карточкой WB')
            if product.part_number and norm(product.part_number)!=norm(row['part_number']):raise ValueError(f'Строка {i}: артикул производителя не совпадает с карточкой WB')
        if row.get('vin'):row['vin_hash']=vin_key(db,store,row.pop('vin'))
    item=FitmentImport(id=str(uuid4()),store_id=store,source=source,verified=verified,complete=complete,rows=rows,digest=digest)
    db.add(item);db.commit()
    return {'id':item.id,'rows':len(rows),'duplicate':False,'unmapped':sum(not row.get('wb_nm_id') for row in rows)}

def catalog_summary(db,store):
    return [{'id':x.id,'source':x.source,'verified':x.verified,'complete':x.complete,'active':x.active,'rows':len(x.rows),'created_at':x.created_at} for x in db.scalars(select(FitmentImport).where(FitmentImport.store_id==store).order_by(FitmentImport.created_at.desc()))]

def contains(text,value):
    # Model/body matches must occupy a complete token sequence, never a prefix.
    words=re.findall(r'[a-zа-яё0-9]+',str(value).casefold())
    pattern=r'(?<![a-zа-яё0-9])'+r'[\s\-]*'.join(map(re.escape,words))+r'(?![a-zа-яё0-9])'
    return bool(words and re.search(pattern,text.casefold()))

def vehicle_match(text,row,hashed):
    if any(row.get(k) for k in ('production_date_from','production_date_to','notes','vin_range')):return False
    if row.get('vin_hash'):return bool(hashed and row['vin_hash']==hashed)
    # A VIN must be decoded in a catalog; an unrelated model phrase cannot replace it.
    if hashed:return False
    if not all(row.get(k) for k in ('make','model','body','year_from','year_to')):return False
    if not all(contains(text,row[k]) for k in ('make','model','body')):return False
    years=re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)',text)
    if len(set(years))!=1 or not row['year_from']<=int(years[0])<=row['year_to']:return False
    return all(not row.get(key) or contains(text,row[key]) for key in ('engine_code','engine','drive','axle','position','pr_code','market','generation'))


def save_question_evidence(db,question,product,body):
    """Owner-reviewed VIN evidence, with separately confirmed reusable OEM crosses."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    vins=set(vehicle_identifiers(question.text));oems=oem_numbers(question.text)
    oe=body.get('oe_number','').strip().upper();oe_brand=body.get('oe_brand','').strip()
    reuse=body.get('remember_cross',False)
    if bool(oe)!=bool(oe_brand):raise ValueError('Укажите вместе оригинальный OEM-номер и его марку')
    if oe and (not re.fullmatch(r'[A-Z0-9][A-Z0-9./-]{3,39}',oe) or not any(c.isdigit() for c in oe) or valid_vehicle_identifier(oe)):
        raise ValueError('Укажите один оригинальный OEM-номер детали')
    if oe and oems and norm(oe) not in oems:raise ValueError('OEM не совпадает с номером в вопросе покупателя')
    if reuse and (len(vins)!=1 or not oe or not oe_brand or not body.get('wb_nm_id') or not body.get('fit')):
        raise ValueError('Для повторного использования подтвердите VIN или Frame, оригинальный OEM и подходящий товар WB')
    row={'brand':body['brand'].strip(),'part_number':body['part_number'].strip(),'wb_nm_id':body.get('wb_nm_id',''),'category':product.category,'fit':body['fit']}
    if not row['brand'] or not row['part_number']:raise ValueError('Нужны бренд и артикул детали')
    if vins:row['vin_hash']=vin_key(db,question.wb_account_id,next(iter(vins)))
    if oe or oems:row['oe_number']=oe or next(iter(oems))
    if oe_brand:row['oe_brand']=oe_brand
    rows=[row]
    if reuse:
        rows.append({'brand':oe_brand,'part_number':oe,'category':product.category,'fit':True,'vin_hash':row['vin_hash']})
        rows.append({k:v for k,v in row.items() if k!='vin_hash'})
    parsed=urlsplit(body['source'])
    # Full VINs belong only to the original question, never to source URLs.
    source=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode([(k,v) for k,v in parse_qsl(parsed.query) if not vehicle_identifiers(v) and k.lower() not in ('vin','frame','identstring')]),''))
    digest=hashlib.sha256(json.dumps([question.wb_account_id,source,rows],sort_keys=True).encode()).hexdigest()
    old=db.scalar(select(FitmentImport).where(FitmentImport.store_id==question.wb_account_id,FitmentImport.digest==digest,FitmentImport.active==True))
    if not old:db.add(FitmentImport(id=str(uuid4()),store_id=question.wb_account_id,source=source,digest=digest,verified=True,complete=False,rows=rows))
    if reuse:
        linked=db.get(Product,row['wb_nm_id'])
        if linked and not linked.part_number:linked.part_number=row['part_number']
    db.commit()
    return {'duplicate':bool(old),'reusable_cross':reuse,'fitment':fitment_answer(db,question,product)}

def fitment_answer(db,question,product):
    store=question.wb_account_id
    vins=vehicle_identifiers(question.text)
    hashed=vin_key(db,store,vins[0]) if len(set(v.upper() for v in vins))==1 else ''
    catalogs=list(db.scalars(select(FitmentImport).where(FitmentImport.store_id==store,FitmentImport.active==True,FitmentImport.verified==True)))
    rows=[(row,c) for c in catalogs for row in c.rows]
    category=norm(product.category)
    direct=lambda row: row.get('wb_nm_id')==product.id or (product.part_number and norm(row.get('brand'))==norm(product.brand) and norm(row.get('part_number'))==norm(product.part_number))
    direct_rows=[row for row,_ in rows if direct(row)]
    if not category:
        categories={norm(row.get('category')) for row in direct_rows if row.get('category')}
        if len(categories)==1:category=categories.pop()
    matches=[(row,c) for row,c in rows if vehicle_match(question.text,row,hashed) and (direct(row) or (category and norm(row.get('category'))==category))]
    anchors=[r for r,_ in matches if r['fit']]
    for row,c in rows:
        if not unrestricted(row):continue
        if not category or norm(row.get('category'))!=category:continue
        if any((row.get('cross_brand') and norm(row['cross_brand'])==norm(a['brand']) and norm(row.get('cross_number'))==norm(a['part_number'])) or (norm(a['part_number']) in oe_numbers(row) and row.get('oe_brand') and norm(row['oe_brand'])==norm(a['brand'])) for a in anchors):
            matches.append((row,c))
    requested_oem=oem_numbers(question.text)
    oem_ambiguous=False
    if requested_oem:
        candidates=[(r,c) for r,c in rows if requested_oem & oe_numbers(r) and (direct(r) or (category and norm(r.get('category'))==category))]
        labels={r['oe_brand'] for r,_ in candidates if r.get('oe_brand')}
        brands={norm(brand) for brand in labels}
        named={norm(brand) for brand in labels if contains(question.text,brand)}
        if named:candidates=[(r,c) for r,c in candidates if norm(r.get('oe_brand','')) in named]
        oem_ambiguous=len(requested_oem)>1 or len(named or brands)>1
        # A supplied VIN or catalog restrictions cannot be overridden by an unscoped cross.
        if hashed:
            matches=[(r,c) for r,c in matches if requested_oem & oe_numbers(r) or (r['fit'] and norm(r['part_number']) in requested_oem)]
        else:
            matches=[(r,c) for r,c in candidates if unrestricted(r) or vehicle_match(question.text,r,'')]
    current=[(r,c) for r,c in matches if direct(r)]
    sources=[{'text':r['brand']+' '+r['part_number'],'source':c.source,'verification_status':'VERIFIED'} for r,c in matches]
    def result(status,text,**extra):return {'status':status,'text':text,'sources':sources,'checked_at':now(),**extra}
    if len(set(v.upper() for v in vins))>1:return result('NEEDS_VIN','Уточните, пожалуйста, один VIN или Frame автомобиля, для которого нужен подбор.')
    if oem_ambiguous:return result('CONFLICT','Уточните, пожалуйста, оригинальный OEM-номер и марку автомобиля или пришлите VIN либо Frame.')
    if current and {r['fit'] for r,_ in current}=={True}:
        return result('FIT','Вам подойдет данный товар.')
    if current and len({r['fit'] for r,_ in current})>1:
        return result('CONFLICT','Уточняем совместимость детали по каталогу.')
    # A cross-number without the manufacturer's brand or exact vehicle application is not proof.
    positives=[(r,c) for r,c in matches if r['fit'] and r.get('wb_nm_id') and r['wb_nm_id']!=product.id]
    alternatives=[]
    for row,catalog in positives:
        candidate=db.get(Product,row['wb_nm_id'])
        listing=db.get(StoreListing,(store,row['wb_nm_id']))
        conflicting=any(other.get('wb_nm_id')==row['wb_nm_id'] and not other['fit'] for other,_ in matches)
        if candidate and candidate.marketplace=='wb' and listing and not conflicting and row.get('stock',1)>0:
            alternatives.append(row)
    if alternatives:
        candidate=sorted(alternatives,key=lambda r:r['wb_nm_id'])[0]
        return result('ALTERNATIVE','Этот товар не подходит. Вам подойдет '+candidate['brand']+' '+candidate['part_number']+', артикул Wildberries '+candidate['wb_nm_id']+'.',product_id=candidate['wb_nm_id']) if current else result('ALTERNATIVE','Вам подойдет '+candidate['brand']+' '+candidate['part_number']+', артикул Wildberries '+candidate['wb_nm_id']+'.',product_id=candidate['wb_nm_id'])
    # Only an explicitly complete catalog can establish absence. Missing evidence is never no stock.
    if current and {r['fit'] for r,_ in current}=={False} and any(c.complete for _,c in current):
        return result('UNAVAILABLE','Для данного автомобиля нет товара в наличии.')
    if requested_oem:return result('NEEDS_CATALOG','Уточняем аналог по указанному оригинальному OEM-номеру. Для точного подбора пришлите, пожалуйста, VIN или Frame автомобиля.')
    if not vins:return result('NEEDS_VIN','Пришлите, пожалуйста, VIN или Frame автомобиля для точного подбора детали.')
    return result('NEEDS_CATALOG','Для точного подбора необходимо подтвердить оригинальный OEM-номер детали по VIN или Frame Вашего автомобиля.')
