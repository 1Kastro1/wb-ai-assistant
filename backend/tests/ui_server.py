"""Disposable local UI test server. Never uses the owner's database."""
import os
import tempfile
os.environ['WB_DATA_DIR'] = tempfile.mkdtemp(prefix='wb-ui-qa-')
os.environ.pop('WB_ENABLE_REAL_PUBLISH', None)
from app.db import Base, engine, Session, Product, Review, set_setting
from app.safety import classify
from argon2 import PasswordHasher
import uvicorn

Base.metadata.create_all(engine)
with Session() as db:
    set_setting(db, 'password_hash', PasswordHasher().hash('UI-test-only-2026'))
    for pid,name,brand,category in [('test-brakes','ТЕСТ · Тормозные колодки','TEST BRAND','Колодки'),('test-aroma','ТЕСТ · Ароматизатор EIKOSHA','EIKOSHA','Ароматизатор')]:
        db.add(Product(id=pid,name=name,brand=brand,category=category))
    db.flush()
    for rid,pid,rating,text in [('test-1','test-brakes',2,'После установки колодки скрипят.'),('test-2','test-brakes',1,'После установки машина стала плохо тормозить.'),('test-3','test-aroma',4,'Запаха практически нет.')]:
        db.add(Review(id=rid,wb_review_id=rid,product_id=pid,rating=rating,text=text,**classify(text,'колодки' if 'brakes' in pid else '')))
    db.commit()
uvicorn.run('app.main:app',host='127.0.0.1',port=8000,access_log=False)
