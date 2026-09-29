import os
import tempfile
os.environ['WB_DATA_DIR'] = tempfile.mkdtemp(prefix='wb-assistant-tests-')
os.environ.pop('WB_ENABLE_REAL_PUBLISH', None)
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from app.db import Base, engine, Session, set_setting, Product, Review
from app.main import app, login_attempts
from app.safety import classify


@pytest.fixture(autouse=True)
def fresh():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    login_attempts.clear()
    with Session() as db:
        set_setting(db, 'password_hash', PasswordHasher().hash('test-password-only-123'))
        db.commit()
    yield


@pytest.fixture
def client():
    with TestClient(app, headers={'Origin': 'http://127.0.0.1:3000'}) as c:
        response = c.post('/auth/login', json={'password': 'test-password-only-123'})
        assert response.status_code == 200, response.text
        c.headers['X-CSRF-Token'] = response.json()['csrf']
        yield c


@pytest.fixture
def seeded():
    with Session() as db:
        db.add(Product(id='p1', name='Колодки', brand='Brand A', category='тормозные колодки'))
        db.add(Product(id='p2', name='Ароматизатор', brand='EIKOSHA', category='ароматизатор'))
        db.flush()
        for rid, pid, rating, text in [('r1','p1',2,'Колодки скрипят'),('r2','p1',1,'После установки машина стала плохо тормозить'),('r3','p2',4,'Запаха почти нет'),('r4','p2',5,'Запаха практически нет')]:
            db.add(Review(id=rid, wb_review_id=rid, product_id=pid, rating=rating, text=text, **classify(text, 'колодки' if pid=='p1' else '')))
        db.commit()

