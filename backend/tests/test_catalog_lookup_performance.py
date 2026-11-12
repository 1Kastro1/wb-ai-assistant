from sqlalchemy import select,event,text
from app.db import Session,Product,Review,Draft,Memory,engine
from app.shared_marketplaces import equivalent_products
from app.services import review_items


def fact(article,*,verified=True):
    return {'text':'Артикул производителя: '+article,'source':'Карточка товара','source_type':'marketplace',
        'verification_status':'VERIFIED' if verified else 'UNVERIFIED'}


def test_direct_and_legacy_paths_preserve_exact_identity_and_use_indexes():
    with Session() as db:
        db.add_all([
            Product(id='source',name='Фильтр',brand=' NITTO ',part_number='A-17',marketplace='wb'),
            Product(id='direct',name='Фильтр',brand='nitto',part_number='A_17',marketplace='ozon'),
            Product(id='legacy',name='Фильтр',brand='NITTO',part_number='',facts=[fact('A-17')],marketplace='yandex'),
            Product(id='ambiguous',name='Фильтр',brand='NITTO',part_number='A17',facts=[fact('B19')]),
            Product(id='unverified',name='Фильтр',brand='NITTO',part_number='',facts=[fact('A-17',verified=False)]),
            Product(id='wrong-brand',name='Фильтр',brand='OTHER',part_number='A17'),
            Product(id='kit',name='Комплект фильтров',brand='NITTO',part_number='A17')]);db.flush()
        assert {p.id for p in equivalent_products(db,db.get(Product,'source'))}=={'source','direct','legacy'}
        direct=db.execute(text("EXPLAIN QUERY PLAN SELECT id FROM products WHERE lower(trim(brand))='nitto' AND upper(replace(replace(replace(replace(replace(part_number,'-',''),' ',''),'.',''),'/',''),'_',''))='A17'")).all()
        legacy=db.execute(text("EXPLAIN QUERY PLAN SELECT id FROM products WHERE lower(trim(brand))='nitto' AND part_number='' AND cast(facts AS VARCHAR) LIKE '%A-17%'")).all()
        assert any('ix_products_brand_article_lookup' in str(row) for row in direct)
        assert any('ix_products_legacy_brand_lookup' in str(row) for row in legacy)
        db.get(Product,'direct').part_number='B-19';db.flush()
        assert {p.id for p in equivalent_products(db,db.get(Product,'source'))}=={'source','legacy'}
        db.get(Product,'legacy').facts=[fact('B19')];db.flush()
        assert [p.id for p in equivalent_products(db,db.get(Product,'source'))]==['source']


def test_page_equivalence_uses_two_queries_without_widening_legacy_spellings():
    from app.shared_marketplaces import equivalent_product_groups
    with Session() as db:
        products=[
            Product(id='compact',name='Фильтр',brand='NITTO',part_number='A17'),
            Product(id='dash',name='Фильтр',brand='nitto',part_number='A-17'),
            Product(id='underscore',name='Фильтр',brand='NITTO',part_number='A_17'),
            Product(id='legacy',name='Фильтр',brand='NITTO',part_number='',facts=[fact('A-17')]),
            Product(id='unverified',name='Фильтр',brand='NITTO',part_number='',facts=[fact('A-17',verified=False)]),
            Product(id='ambiguous',name='Фильтр',brand='NITTO',part_number='A17',facts=[fact('B19')]),
            Product(id='kit',name='Комплект фильтров',brand='NITTO',part_number='A17'),
            Product(id='other',name='Фильтр',brand='OTHER',part_number='A17')]
        db.add_all(products);db.flush()
        expected={p.id:{c.id for c in equivalent_products(db,p)} for p in products}
        queries=[]
        def record(conn,cursor,statement,parameters,context,many):
            if statement.lstrip().startswith('SELECT'):queries.append(statement)
        event.listen(engine,'before_cursor_execute',record)
        try:groups=equivalent_product_groups(db,products)
        finally:event.remove(engine,'before_cursor_execute',record)
        assert len(queries)==2
        assert {key:{p.id for p in value} for key,value in groups.items()}==expected
        assert 'legacy' not in expected['compact'] and 'legacy' in expected['dash']
        db.get(Product,'legacy').facts=[fact('B19')];db.flush()
        groups=equivalent_product_groups(db,products)
        assert 'legacy' not in {p.id for p in groups['dash']}


def test_page_does_not_resolve_rules_for_reviews_without_drafts(monkeypatch):
    with Session() as db:
        db.add_all(Product(id='p'+str(i),name='Товар',brand='NITTO',part_number='A'+str(i)) for i in range(30))
        db.add(Memory(id='rule',text='Спасибо за отзыв.',kind='REVIEW_RULE',scope='global'));db.flush()
        db.add_all(Review(id='r'+str(i),wb_review_id='r'+str(i),wb_account_id='owner',product_id='p'+str(i),text='Все хорошо',rating=5) for i in range(30));db.flush()
        db.add(Draft(id='draft',review_id='r0',text='Добрый день! Спасибо за отзыв.',original='Добрый день! Спасибо за отзыв.'));db.flush()
        calls=[]
        def rules(session,product,**kwargs):calls.append(product.id);return []
        monkeypatch.setattr('app.services.matching_owner_rules',rules)
        items=review_items(db,list(db.scalars(select(Review).order_by(Review.id))))
        assert len(items)==30 and calls==['p0']
        assert sum(item['draft'] is not None for item in items)==1
