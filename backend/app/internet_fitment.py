"""Bounded public-catalog lookup. Website text is data, never model instructions."""
import asyncio
import hashlib
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, parse_qs, urlencode
import httpx
from sqlalchemy import select
from .db import Audit, Session, Product, StoreListing, FitmentImport, now, setting, set_setting
from .vehicle_identifiers import mask_frames
from .shared_marketplaces import row_product_id, resolve_listing, product_reference
from .fitment_catalog import VIN, vehicle_identifiers, norm, vin_key, fitment_answer, oe_numbers
from .fitment_categories import EXTRA_PARTS, category_plan

PARTS = {
    'pitman_arm': (r'сошк|pitman', ('Steering', 'Steering system', 'Steering gear'), ('arm, pitman','pitman arm','arm, steering pitman','steering pitman arm','arm sub-assy, pitman','arm assy, pitman')),
    'tie_rod_end': (r'наконечник.*рулев|рулев.*наконечник|tie rod end', ('Steering', 'Steering system', 'Steering gear'), ('tie rod end','end sub-assy, tie rod','end sub-assy, tie rod, rh','end sub-assy, tie rod, lh')),
    'oil_filter': (r'фильтр.*маслян|маслян.*фильтр', 'Filters', ('engine oil filter','oil filter')),
    'cabin_filter': (r'фильтр.*салон|салон.*фильтр', 'Filters', ('interior air filter','cabin air filter','cabin filter')),
    'air_filter': (r'фильтр.*воздуш|воздуш.*фильтр', 'Filters', ('air filter','engine air filter')),
    'fuel_filter': (r'фильтр.*топлив|топлив.*фильтр', 'Filters', ('fuel filter',)),
    'spark_plug': (r'свеч.*зажиган', 'Ignition system', ('spark plug',)),
    'brake_pad': (r'колодк.*тормоз|тормоз.*колодк', 'Brake system', ('brake pad set','brake pad','disc brake pad')),
    'brake_disc': (r'диск.*тормоз|тормоз.*диск', 'Brake system', ('brake disc','brake rotor')),
}
PARTS = {**EXTRA_PARTS, **PARTS}
STATUSES = {
    'FOUND':'Оригинальный OEM найден в каталоге по VIN или Frame.',
    'MULTIPLE_VEHICLES':'Каталог вернул несколько вариантов автомобиля. Нужна проверка модификации.',
    'NEEDS_REVIEW':'Найдены сведения, но применяемость требует проверки.',
    'ACCESS_REQUIRED':'PartSouq ограничил доступ с сервера. Проверьте подбор в Uniqom через браузер.',
    'NOT_FOUND':'Каталог не вернул подходящих данных. Это не означает отсутствие товара.',
    'UNAVAILABLE':'Автоматическая проверка в PartSouq сейчас недоступна. Подбор через Uniqom в браузере доступен отдельно.',
    'UNSUPPORTED':'Для этой категории нужен ручной выбор детали в каталоге.',
    'CATALOG_SELECTION':'Откройте автомобиль в Uniqom, выберите нужный узел и сохраните проверенный OEM и аналог. Подбор доступен для любой категории.',
    'NEEDS_VIN':'Для поиска нужен один полный VIN или Frame автомобиля.',
}
LOOKUP_LIMIT = asyncio.Semaphore(2)


class Node:
    def __init__(self,tag='',attrs=()):self.tag=tag;self.attrs=dict(attrs);self.children=[]
    def text(self):return ' '.join(x.text() if isinstance(x,Node) else x for x in self.children if not isinstance(x,Node) or x.tag not in ('script','style','noscript'))
    def find(self,tag=None,cls=None):
        found=[]
        for child in self.children:
            if isinstance(child,Node):
                if (not tag or child.tag==tag) and (not cls or cls in child.attrs.get('class','').split()):found.append(child)
                if child.tag not in ('script','style','noscript'):found.extend(child.find(tag,cls))
        return found


class Document(HTMLParser):
    def __init__(self,html):
        super().__init__(convert_charrefs=True);self.root=Node();self.stack=[self.root];self.count=0;self.feed(html)
    def handle_starttag(self,tag,attrs):
        self.count+=1
        if self.count>50000 or len(self.stack)>256:raise ValueError('CATALOG_SIZE')
        node=Node(tag,attrs);self.stack[-1].children.append(node)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):self.stack.append(node)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag:self.stack=self.stack[:i];break
    def handle_data(self,value):self.stack[-1].children.append(value)


def target_part(product):
    return next((key for key,(pattern,_,__) in PARTS.items() if re.search(pattern,product.name+' '+product.category,re.I)),None)


def safe_catalog_url(url):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname!='partsouq.com' or parsed.port not in (None,443) or parsed.username or parsed.password:raise ValueError('CATALOG_URL')
    if parsed.path not in ('/en/search/all','/en/partcatalog/groups/','/en/partcatalog/diagram/'):raise ValueError('CATALOG_URL')
    if len(url)>3000:raise ValueError('CATALOG_URL')
    return url


def source_url(url):
    # Catalog links can contain VINs in q AND criteria. Never persist either field.
    parsed=urlsplit(url);query=parse_qs(parsed.query)
    kept={k:v[0] for k,v in query.items() if k in ('catalogId','carId','groupId','schemaId','page')}
    return 'https://partsouq.com'+parsed.path+('?' + urlencode(kept) if kept else '')


def context_url(url,vin,car_id):
    safe_catalog_url(url);query=parse_qs(urlsplit(url).query)
    if query.get('q')!=[vin] or query.get('carId')!=[car_id]:raise ValueError('CATALOG_CONTEXT')
    return url


class PublicCatalog:
    def __init__(self,transport=None):self.transport=transport
    async def read(self,url,json_response=False):
        host=urlsplit(url).hostname
        if host=='partsouq.com':safe_catalog_url(url)
        elif not re.fullmatch(r'https://vpic\.nhtsa\.dot\.gov/api/vehicles/DecodeVinValues/[A-HJ-NPR-Z0-9]{17}\?format=json',url):raise ValueError('CATALOG_URL')
        started=time.monotonic();status='NETWORK_ERROR'
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(7,connect=4),trust_env=False,follow_redirects=False,transport=self.transport) as client:
                async with client.stream('GET',url,headers={'Accept':'application/json' if json_response else 'text/html'}) as response:
                    status=str(response.status_code)
                    if response.status_code in (301,302,303,307,308,401,403,429):raise ValueError('ACCESS_REQUIRED')
                    if not response.is_success:raise ValueError('UNAVAILABLE')
                    raw=bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw)>2*1024*1024:raise ValueError('CATALOG_SIZE')
                    text=raw.decode('utf-8',errors='replace')
                    if not json_response and re.search(r'cf-chl-|challenge-platform|verify you are human|captcha|access denied',text,re.I):raise ValueError('ACCESS_REQUIRED')
                    if json_response:
                        import json
                        return json.loads(text)
                    return Document(text).root
        except httpx.HTTPError:raise ValueError('UNAVAILABLE') from None
        finally:
            with Session() as audit:
                audit.add(Audit(provider='catalog',host=host,operation='vin_lookup',status=status,duration_ms=int((time.monotonic()-started)*1000)));audit.commit()

    async def decode(self,vin):
        data=await self.read('https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/'+vin+'?format=json',True)
        row=(data.get('Results') or [{}])[0]
        fields={'make':'Make','model':'Model','year':'ModelYear','engine_code':'EngineModel','body':'BodyClass','drive':'DriveType','fuel':'FuelTypePrimary'}
        return {**{key:str(row.get(field) or '')[:120] for key,field in fields.items()},'complete':str(row.get('ErrorCode'))=='0','source':'https://vpic.nhtsa.dot.gov/'}

    async def lookup(self,vin,part):
        url='https://partsouq.com/en/search/all?'+urlencode({'q':vin})
        root=await self.read(url)
        vehicles={}
        for row in root.find('tr'):
            cells=row.find('td')
            for link in row.find('a'):
                href=urljoin(url,link.attrs.get('href',''));parsed=urlsplit(href);params=parse_qs(parsed.query)
                if parsed.path!='/en/partcatalog/groups/' or params.get('q')!=[vin] or not params.get('carId'):continue
                safe_catalog_url(href)
                key=params['carId'][0]
                vehicles[key]={'name':link.text().strip() or (cells[4].text().strip() if len(cells)>4 else ''),'make':cells[1].text().strip() if len(cells)>1 else '', 'url':href}
        result={'vehicles':[{'name':v['name'],'make':v['make'],'source':source_url(v['url'])} for v in vehicles.values()],'oem_candidates':[],'sources':[{'source':'https://partsouq.com/','text':'Результат VIN-поиска PartSouq','verification_status':'WEB_UNVERIFIED'}]}
        if not vehicles:return {**result,'status':'NOT_FOUND'}
        if len(vehicles)!=1:return {**result,'status':'MULTIPLE_VEHICLES'}
        car_id,vehicle=next(iter(vehicles.items()));root=await self.read(vehicle['url'])
        # A search box containing the VIN is not a verified vehicle summary.
        summaries=root.find('table','part-catalog-vehicle-details')
        if len(summaries)!=1 or vin not in summaries[0].text().upper():return {**result,'status':'NEEDS_REVIEW'}
        category=PARTS[part][1]
        categories=(category,) if isinstance(category,str) else category
        links=[a for a in root.find('a') if a.text().strip() in categories]
        if len(links)!=1:return {**result,'status':'NEEDS_REVIEW'}
        group_url=context_url(urljoin(vehicle['url'],links[0].attrs['href']),vin,car_id);root=await self.read(group_url)
        if any(a.text().strip().lower()=='next' for a in root.find('a')):return {**result,'status':'NEEDS_REVIEW'}
        diagrams=[]
        for article in root.find('article'):
            if not any(label in article.text().lower() for label in PARTS[part][2]):continue
            for link in article.find('a'):
                href=urljoin(group_url,link.attrs.get('href',''))
                if urlsplit(href).path=='/en/partcatalog/diagram/':diagrams.append(context_url(href,vin,car_id))
        diagrams=list(dict.fromkeys(diagrams))
        if not 1<=len(diagrams)<=4:return {**result,'status':'NEEDS_REVIEW'}
        numbers=[];uncertain=False
        for diagram in diagrams:
            root=await self.read(diagram)
            summaries=root.find('table','part-catalog-vehicle-details')
            if len(summaries)!=1 or vin not in summaries[0].text().upper():return {**result,'status':'NEEDS_REVIEW'}
            found=[]
            for article in root.find('article','part-catalog-part-row'):
                labels=article.find('strong');parts=article.find('a','part-catalog-part-row__number')
                if len(labels)!=1 or labels[0].text().strip().lower() not in PARTS[part][2] or len(parts)!=1:continue
                oe=parts[0].attrs.get('data-part-number','')
                if norm(oe)!=norm(parts[0].text()) or not re.fullmatch(r'[A-Z0-9][A-Z0-9./-]{3,29}',oe,re.I):continue
                details=article.text()
                if part.endswith(('_front','_rear')):
                    details=details.replace(labels[0].text(),'')
                if re.search(r'\b(?:from|until|except|only|vin|pr[- ]code|left|right|front|rear)\b',details,re.I):uncertain=True
                found.append(norm(oe));result['oem_candidates'].append({'oe_number':oe,'oe_brand':vehicle['make'],'description':labels[0].text().strip(),'source':source_url(diagram)})
            if not found:uncertain=True
            numbers.extend(found)
        if not uncertain and len(set(numbers))==1:
            return {**result,'status':'FOUND'}
        return {**result,'status':'NEEDS_REVIEW'}


catalog = PublicCatalog()


def cache_key(db,question,product):
    vins=set(vehicle_identifiers(question.text))
    fingerprint=vin_key(db,question.wb_account_id,next(iter(vins))) if len(vins)==1 else hashlib.sha256(question.text.encode()).hexdigest()
    return 'internet-fitment:'+question.wb_account_id+':'+hashlib.sha256((fingerprint+product.id+norm(product.category)+(target_part(product) or 'all-categories')+'v3').encode()).hexdigest()


def last_research(db,question,product):
    return setting(db,cache_key(db,question,product),{}).get('result',{})


def own_candidates(db,product,oems):
    choices={}
    for item in db.scalars(select(FitmentImport).where(FitmentImport.store_id==product['store'],FitmentImport.active==True,FitmentImport.verified==True)):
        for row in item.rows:
            pid=row_product_id(row)
            if not pid or not row['fit'] or not oems & oe_numbers(row) or norm(row.get('category'))!=norm(product['category']) or row.get('stock',1)<=0:continue
            listed=resolve_listing(db,product['store'],pid)
            if listed and not row.get('product_id') and listed.marketplace!='wb':continue
            if not listed or norm(listed.brand)!=norm(row['brand']):continue
            reference,url=product_reference(listed)
            choices[listed.id]={'product_id':listed.id,'wb_nm_id':listed.id if listed.marketplace=='wb' else '', 'brand':row['brand'],'part_number':row['part_number'],'name':listed.name,'image_url':listed.image_url,'article_label':reference,'url':url,'source':item.source,'requires_review':True}
    return list(choices.values())[:10]


async def research_question(db,question,product,force=False):
    original=(question.text,question.product_id)
    vins=set(vehicle_identifiers(question.text));part=target_part(product)
    if len(vins)!=1:return {'status':'NEEDS_VIN','message':STATUSES['NEEDS_VIN'],'checked_at':now()}
    key=cache_key(db,question,product);previous=setting(db,key,{})
    ttl=7*86400 if previous.get('result',{}).get('status')=='FOUND' else 300
    if previous and time.time()-previous.get('at',0)<(60 if force else ttl):return {**previous['result'],'cached':True}
    vin=next(iter(vins));result={'status':'UNAVAILABLE','provider':'partsouq' if part else 'uniqom_browser','sources':[],'oem_candidates':[],'vehicles':[],'coverage':category_plan(product,part)}
    async def decode():
        if not part:return {}
        try:return await catalog.decode(vin) if VIN.fullmatch(vin) else {}
        except (ValueError,TypeError,KeyError):return {}
    async def lookup():
        if not part:return {**result,'status':'CATALOG_SELECTION'}
        try:return await catalog.lookup(vin,part)
        except (ValueError,TypeError,KeyError) as error:return {**result,'status':str(error) if str(error) in STATUSES else 'UNAVAILABLE'}
    try:
        async with asyncio.timeout(18):
            async with LOOKUP_LIMIT:
                vehicle,found=await asyncio.gather(decode(),lookup())
                result.update(found);result['vehicle']=vehicle
    except TimeoutError:result['status']='UNAVAILABLE'
    db.refresh(question)
    if original!=(question.text,question.product_id):return {'status':'NEEDS_REVIEW','message':'Вопрос изменился во время поиска. Запустите проверку заново.','checked_at':now()}
    result.update(checked_at=now(),message=STATUSES.get(result['status'],STATUSES['NEEDS_REVIEW']),cached=False)
    if result['status']=='FOUND':
        candidate=result['oem_candidates'][0]
        # Only an unambiguous structured VIN catalog result creates an exact VIN anchor.
        row={'brand':candidate['oe_brand'],'part_number':candidate['oe_number'],'category':product.category,'fit':True,'vin_hash':vin_key(db,question.wb_account_id,vin)}
        digest=hashlib.sha256((key+norm(row['brand'])+norm(row['part_number'])).encode()).hexdigest()
        old=db.scalar(select(FitmentImport).where(FitmentImport.store_id==question.wb_account_id,FitmentImport.digest==digest))
        if not old:
            from uuid import uuid4
            db.add(FitmentImport(id=str(uuid4()),store_id=question.wb_account_id,source=candidate['source'],digest=digest,verified=True,complete=False,rows=[row]));db.flush()
        result['sources']=[{'source':candidate['source'],'text':candidate['oe_brand']+' OEM '+candidate['oe_number'],'verification_status':'VERIFIED'}]
        result['answer']=fitment_answer(db,question,product)
    oems={norm(x['oe_number']) for x in result.get('oem_candidates',[])}
    result['our_candidates']=own_candidates(db,{'store':question.wb_account_id,'category':product.category},oems)
    # Defense in depth: no VIN in stored messages, catalog metadata or query links.
    import json
    result=json.loads(mask_frames(json.dumps(result,ensure_ascii=False).replace(vin,'[VIN / Frame скрыт]')))
    set_setting(db,key,{'at':time.time(),'result':result});db.commit()
    return result

