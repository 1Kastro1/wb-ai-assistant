from app.db import Session, Product, Review, Draft


def seed():
    with Session() as db:
        db.add_all([Product(id='a',name='One',brand='EIKOSHA',category='Ароматизаторы'),Product(id='b',name='Two',brand='KANGAROO',category='Химия')]);db.flush()
        for i in range(8):
            db.add(Review(id=str(i),wb_review_id=str(i),product_id='a' if i<6 else 'b',rating=2 if i%2==0 else 5,text='Текст' if i!=2 else ' \n\t',is_answered=i==4,manual=i==0))
        db.add(Review(id='other',wb_review_id='other',product_id='b',wb_account_id='other-store',rating=1,text='Текст'))
        db.add(Review(id='question',wb_review_id='question',product_id='b',rating=1,text='Вопрос',marketplace='ozon_question'))
        db.commit()


def test_intersection_before_pagination_and_no_store_leak(client):
    seed()
    params=[('ratings','1'),('ratings','2'),('ratings','3'),('text_status','with_text'),('brands','EIKOSHA'),('answer_status','unanswered'),('limit','1')]
    first=client.get('/reviews/page',params=params).json()
    assert first['total']==1 and [r['id'] for r in first['items']]==['0']
    result=client.get('/reviews/page',params=[('ratings','2'),('text_status','with_text'),('limit','1')]).json()
    assert result['total']==3 and result['has_more']
    second=client.get('/reviews/page',params=[('ratings','2'),('text_status','with_text'),('limit','1'),('offset','1')]).json()
    assert second['total']==3 and result['items'][0]['id']!=second['items'][0]['id']
    assert client.get('/reviews/page').json()['total']==8


def test_multiple_values_or_between_sections_and_manual(client):
    seed()
    params=[('brands','EIKOSHA'),('brands','KANGAROO'),('categories','Химия'),('ratings','2'),('text_status','with_text')]
    assert [r['id'] for r in client.get('/reviews/page',params=params).json()['items']]==['6']
    assert [r['id'] for r in client.get('/reviews/page',params={'manual_only':'true','text_status':'with_text','brands':'EIKOSHA'}).json()['items']]==['0']


def test_facets_use_entire_store_and_reject_invalid_filters(client):
    seed()
    assert client.get('/reviews/facets').json()=={'brands':['EIKOSHA','KANGAROO'],'categories':['Ароматизаторы','Химия']}
    for params in ({'ratings':'0'},{'ratings':'6'},{'text_status':'invented'},{'answer_status':'other'}):
        assert client.get('/reviews/page',params=params).status_code==422


def test_generated_filter_uses_saved_text_before_pagination_independent_of_verdict(client):
    seed()
    with Session() as db:
        for rid in ('0','1','4','other','question'):
            db.add(Draft(id='d-'+rid,review_id=rid,text='Сохранённый ответ',original='Сохранённый ответ',quality={'passed':False}))
        db.add(Draft(id='blank',review_id='2',text=' \n\t',original=''))
        db.commit()
    result=client.get('/reviews/page',params={'draft_status':'generated','limit':1}).json()
    assert result['total']==3 and result['has_more'] and len(result['items'])==1
    ids={r['id'] for r in client.get('/reviews/page',params={'draft_status':'generated'}).json()['items']}
    assert ids=={'0','1','4'}
    combined=client.get('/reviews/page',params={'draft_status':'generated','answer_status':'unanswered','ratings':2,'manual_only':True}).json()
    assert combined['total']==1 and combined['items'][0]['id']=='0'
    missing=client.get('/reviews/page',params={'draft_status':'missing'}).json()
    assert missing['total']==5 and '2' in {r['id'] for r in missing['items']}
    assert client.get('/reviews/page',params={'draft_status':'invalid'}).status_code==422
