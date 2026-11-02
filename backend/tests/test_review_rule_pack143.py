from types import SimpleNamespace as NS
from unittest.mock import patch
from sqlalchemy import select
from app.db import Session, Memory, Job, Product, Review, Draft
from app.owner_reply_rules import matching_owner_rules, category_rule_scopes
from app.rule_execution import positive_template, verified_positive_reply, PREVIOUS_RATING_BODIES
from app.owner_reply_policy import apply_signature, apply_local_preferences, context_constraints
from app import review_rule_pack143 as pack


def test_group_scope_uses_product_metadata_and_keeps_markets_and_audiences(client):
    with Session() as db:
        db.add_all([Memory(id='filter',text='Filter policy',kind='REVIEW_RULE',scope='category:Фильтры'),
            Memory(id='other',text='Brake policy',kind='REVIEW_RULE',scope='category:Тормозные колодки и диски'),
            Memory(id='question',text='Question policy',kind='QUESTION_RULE',scope='category:Фильтры')])
        db.commit()
        for market in ('wb','ozon','yandex'):
            p=Product(id=market,name='Фильтр масляный NITTO',brand='NITTO',category='Разные категории',marketplace=market)
            assert matching_owner_rules(db,p)==['Filter policy']
            assert matching_owner_rules(db,p,True)==['Question policy']
        assert 'category:Фильтры' not in category_rule_scopes(NS(name='Ароматизатор',brand='EIKOSHA',category=''))
        assert 'category:Автохимия и очистители' not in category_rule_scopes(NS(name='Щётка стеклоочистителя',category='Щётки'))


def test_fusso_instruction_is_only_for_exact_product_family():
    for name,brand,expected in [('Воск Fusso 12 Months для тёмных авто','SOFT99',True),
        ('Воск Fusso 12 Months для светлых авто','Soft99',True),('Очиститель стёкол','SOFT99',False),
        ('Fusso 12 Months','OTHER',False),('Воск Fusso Coat Speed','SOFT99',False)]:
        assert ('category:SOFT99 Fusso 12 Months' in category_rule_scopes(NS(name=name,brand=brand,category=''))) == expected


def test_consolidated_rules_keep_fast_safe_warm_templates(client):
    with Session() as db:
        db.add_all(Memory(id='pack'+str(i),text=r['text'],kind=r['kind'],scope=r['scope']) for i,r in enumerate(pack.PACK['rules']))
        db.commit()
        for market in ('wb','ozon','yandex'):
            for name,brand in [('Очиститель Profoam 2000','KANGAROO'),('Фильтр масляный','NITTO'),('Ароматизатор GIGA','EIKOSHA')]:
                p=Product(id='example',name=name,brand=brand,category='',marketplace=market)
                rules=matching_owner_rules(db,p)
                assert set(context_constraints(rules)['greetings'])=={'Здравствуйте.','Добрый день!'}
                review=NS(id='example',text='',rating=5,risk='NORMAL',marketplace=market)
                answer=positive_template(review,rules,product=p)
                assert answer and answer.endswith('С уважением ГК Юником.')
                assert verified_positive_reply(answer,review,rules,product=p)
                for body in PREVIOUS_RATING_BODIES:
                    old,_=apply_local_preferences('Добрый день! '+body,review,rules)
                    assert verified_positive_reply(apply_signature(old,rules),review,rules,product=p)
                review.text='Хорошо, но протекло'
                assert positive_template(review,rules,product=p) is None


def test_migration_is_guarded_reversible_idempotent_and_question_safe(client):
    tiny={'expected_hashes':[pack.digest('Original review policy')],
        'rules':[{'text':'Replacement review policy','kind':'REVIEW_RULE','scope':'global','source_index':0}]}
    with Session() as db, patch.object(pack,'PACK',tiny):
        db.add_all([Memory(id='old',text='Original review policy',kind='REVIEW_RULE'),
            Memory(id='question',text='Keep question policy',kind='QUESTION_RULE')]);db.commit()
        assert not pack.migrate(db)['applied']
        assert db.get(Memory,'old').enabled
        assert pack.migrate(db,apply=True)['rules']==1
        db.commit()
        assert not db.get(Memory,'old').enabled
        assert db.get(Memory,'question').enabled
        assert pack.migrate(db,apply=True)['already_applied']
        assert len(list(db.scalars(select(Memory))))==3
