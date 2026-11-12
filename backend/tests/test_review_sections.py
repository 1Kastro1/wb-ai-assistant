import pytest
from app.review_sections import review_sections
from app.db import Session, StoreProfile
from app.integrations import upsert_market_review
from app.services import review_items


@pytest.mark.parametrize('market,keys', [
    ('wb', ('pros','cons','text')), ('ozon', ('positive','negative','text')),
    ('yandex', ('advantages','disadvantages','comment'))])
def test_marketplace_boundaries_survive_storage_and_serialization(market, keys):
    parts=review_sections(dict(zip(keys, ('Крепкие','Скрипят','После недели использования'))),market)
    with Session() as db:
        store=StoreProfile(id=market,name=market,provider=market);db.add(store);db.flush()
        row=upsert_market_review(db,store,'r','p','Щётки','',3,
            'Крепкие\nСкрипят\nПосле недели использования','2026-11-12',sections=parts)
        db.commit()
        item=review_items(db,[row])[0]
        assert item['sections']=={'advantages':'Крепкие','disadvantages':'Скрипят','comment':'После недели использования'}
        assert item['text']=='Крепкие\nСкрипят\nПосле недели использования'
        # An abbreviated API response must not destroy known field boundaries.
        upsert_market_review(db,store,'r','p','','',3,row.text,row.created_at)
        db.commit();assert row.sections==parts


def test_absent_fields_are_not_invented_but_explicit_empty_fields_are_kept():
    assert review_sections({},'yandex')=={}
    assert review_sections({'comment':'Комментарий'},'yandex')=={
        'advantages':'','disadvantages':'','comment':'Комментарий'}
