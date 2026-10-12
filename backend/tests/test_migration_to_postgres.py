from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Account, Base, Draft, LoginSession, Product, Review, Setting, User
from migrate_to_postgres import copy_database


def test_team_reviews_drafts_survive_without_windows_credentials(tmp_path):
    source_file = tmp_path / 'source.db'
    source = create_engine('sqlite:///' + source_file.as_posix())
    target = create_engine('sqlite:///' + (tmp_path / 'target.db').as_posix())
    Base.metadata.create_all(source)
    Base.metadata.create_all(target)

    with Session(source) as db:
        db.add(User(id='staff', username='staff', display_name='Staff', password_hash='hashed', role='member'))
        db.add(Product(id='product', name='Test product'))
        db.add(Review(id='review', wb_review_id='remote', product_id='product', rating=2, text='Issue'))
        db.add(Draft(id='draft', review_id='review', text='Reply', original='Reply'))
        db.add(Account(id='owner', encrypted_token='WINDOWS-DPAPI-CIPHERTEXT'))
        db.add(LoginSession(id='session', expires=9999999999, csrf='csrf', user_id='staff'))
        db.add(Setting(key='safe_mode', value=False))
        db.add(Setting(key='test_mode', value=False))
        db.add(Setting(key='automation_schedule', value={'enabled': True}))
        db.commit()

    copied = copy_database(source_file, target)
    with Session(target) as db:
        assert copied['users'] == 1
        assert copied['reviews'] == 1
        assert copied['generated_answers'] == 1
        assert db.get(User, 'staff').password_hash == 'hashed'
        assert db.get(Draft, 'draft').text == 'Reply'
        assert db.execute(select(Account)).all() == []
        assert db.execute(select(LoginSession)).all() == []
        assert db.get(Setting, 'safe_mode').value is True
        assert db.get(Setting, 'test_mode').value is True
        assert db.get(Setting, 'automation_schedule') is None

    source.dispose()
    target.dispose()
