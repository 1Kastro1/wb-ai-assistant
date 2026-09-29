import json
import io
import pytest
from app.compatibility import evaluate,validate_vin,normalize_oe,import_catalog,check_compatibility
from app.db import Session,Product,BrandPreference

VEHICLE={'make':'Test','model':'Car','generation':'Gen','engine_code':'E1','year':2018,'axle':'front','brake_system':'X','verified':True,'source':'Test fixture'}
APP={'make':'Test','model':'Car','generation':'Gen','engine_code':'E1','year_from':2017,'year_to':2020,'axle':'front','brake_system':'X'}


@pytest.mark.parametrize('value',['','123','JTMIOQFV10D123456','JTMAB3FV10D123456!'])
def test_vin_validation(value):
    with pytest.raises(ValueError):validate_vin(value)


def test_vin_masking_and_local_lookup(client):
    vin='JTMAB3FV10D1234567'
    # 17-character fixture, no real vehicle identity implied.
    vin='JTMAB3FV10D123456'
    assert len(vin)==17
    result=client.post('/vin',json={'vin':vin}).json()
    assert result['status']=='INSUFFICIENT_DATA'
    assert vin not in json.dumps(result)
    assert client.post('/vin',json={'vin':vin,'profile':VEHICLE}).status_code==200
    result=client.post('/vin',json={'vin':vin}).json()
    assert result['status']=='LOCAL_MATCH' and result['profile']==VEHICLE


def test_exact_fit_and_oe():
    assert evaluate(VEHICLE,APP,True)[0]=='VERIFIED_FIT'
    assert normalize_oe(' 04465-12345 ')==normalize_oe('04465 12345')


def test_insufficient_data():
    assert evaluate({'make':'Toyota','model':'Camry','year':2018},APP,True)[0]=='INSUFFICIENT_DATA'
    assert evaluate(VEHICLE,APP,False)[0]=='INSUFFICIENT_DATA'


def test_conditional_fit():
    vehicle={k:v for k,v in VEHICLE.items() if k!='brake_system'}
    status,conditions=evaluate(vehicle,APP,True)
    assert status=='CONDITIONAL_FIT' and 'brake_system: X' in conditions
    assert evaluate(VEHICLE,{**APP,'notes':'Только при дополнительной проверке'},True)[0]=='CONDITIONAL_FIT'


def test_not_fit():
    assert evaluate({**VEHICLE,'year':2010},APP,True)[0]=='VERIFIED_NOT_FIT'


def test_catalog_conflict():
    with Session() as db:
        for name,engine in [('source-a','E1'),('source-b','E2')]:
            import_catalog(db,json.dumps([{**APP,'brand':'A','part_number':'P','engine_code':engine}]).encode(),'test.json',name,True)
        result=check_compatibility(db,VEHICLE,part_number='P')
        assert result['items'][0]['status']=='CATALOG_CONFLICT'
        assert not result['items'][0]['recommendable']


@pytest.mark.parametrize('format',['json','csv','xlsx'])
def test_import_formats_and_dedup(format):
    row={**APP,'brand':'A','part_number':'P','oe_number':'OE-1','cross_number':'CROSS-1'}
    if format=='json':raw=json.dumps([row]).encode()
    elif format=='csv':raw=(','.join(row)+'\n'+','.join(map(str,row.values()))).encode()
    else:
        from openpyxl import Workbook
        wb=Workbook();wb.active.append(list(row));wb.active.append(list(row.values()));buf=io.BytesIO();wb.save(buf);raw=buf.getvalue()
    with Session() as db:
        assert import_catalog(db,raw,'catalog.'+format,'fixture',True)['rows']==1
        assert import_catalog(db,raw,'catalog.'+format,'fixture',True)['duplicate']
        assert check_compatibility(db,VEHICLE,oe='oe 1')['items'][0]['status']=='VERIFIED_FIT'


def test_same_brand_order_and_exclude_not_fit():
    with Session() as db:
        for i,brand,code in [(1,'A','E1'),(2,'B','E1'),(3,'C','E2')]:
            db.add(Product(id=str(i),name='Test',brand=brand))
            db.add(BrandPreference(brand=brand,enabled=True))
            db.commit()
            import_catalog(db,json.dumps([{**APP,'brand':brand,'part_number':'P','engine_code':code,'wb_nm_id':str(i)}]).encode(),'test.json',brand,True)
        result=check_compatibility(db,VEHICLE,same_brand='A')
        assert [r['brand'] for r in result['items'] if r['recommendable']]==['A','B']


def test_unverified_product_mapping_cannot_borrow_trusted_fit():
    with Session() as db:
        db.add(Product(id='1',name='Test',brand='A'))
        db.commit()
        import_catalog(db,json.dumps([{**APP,'brand':'A','part_number':'P','wb_nm_id':'1'}]).encode(),'test.json','unverified',False)
        import_catalog(db,json.dumps([{**APP,'brand':'A','part_number':'P'}]).encode(),'test.json','trusted',True)
        result=check_compatibility(db,VEHICLE,same_brand='A')
        assert not result['items'][0]['recommendable']
