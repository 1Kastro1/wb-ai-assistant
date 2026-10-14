"""Question VIN sync and authenticated display use a disposable database only."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Account, Base, Review, StoreProfile, User, set_setting
from app.integrations import ozon, wb, yandex
from app.safety import mask_vin, validate_reply

VIN = 'KMHJT81VDFU123855'
QUESTION = 'Подойдёт ли фильтр для Hyundai Tucson VIN ' + VIN + '?'


class QuestionVinTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool,
                                    connect_args={'check_same_thread': False})
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as db:
            db.add(User(id='actor', username='vin-tester', display_name='Tester',
                        role='owner', password_hash='unused'))
            for store_id, provider in [('owner', 'wb'), ('ozon-store', 'ozon'),
                                       ('yandex-store', 'yandex')]:
                db.add(StoreProfile(id=store_id, name=provider, provider=provider,
                                    config={'client_id': '123', 'business_id': '456'}))
                db.add(Account(id=store_id, encrypted_token='fake-encrypted-token'))
            db.commit()
        self.auth = {'role': 'owner', 'username': 'vin-tester', 'user_id': 'actor',
                     'permissions': ['*'], 'store_ids': ['*']}
        self.previous = dict(main.app.dependency_overrides)

        def database():
            with self.factory() as db:
                yield db

        def authorize(request: Request):
            request.state.actor = {'user_id': 'actor', 'username': 'vin-tester'}
            return self.auth

        main.app.dependency_overrides[main.database] = database
        main.app.dependency_overrides[main.authorize] = authorize
        self.session_patch = patch('app.main.Session', self.factory)
        self.session_patch.start()
        self.token_patch = patch('app.integrations.decrypt_secret',
                                 return_value='fake-token')
        self.token_patch.start()
        # No lifespan: migrations and background workers must not use the real DB.
        self.client = TestClient(main.app, headers={'Origin': main.ORIGIN})

    def tearDown(self):
        self.client.close()
        self.token_patch.stop()
        self.session_patch.stop()
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(self.previous)
        self.engine.dispose()

    def sync_wb(self):
        async def request(provider, operation, **kwargs):
            self.assertEqual((provider, operation), ('wb', 'questions'))
            rows = [] if kwargs['params']['isAnswered'] == 'true' else [{
                'id': 'vin-question', 'text': '  ' + QUESTION + '  ',
                'createdDate': '2026-10-02T10:00:00Z',
                'productDetails': {'nmId': 77, 'productName': 'Фильтр', 'brandName': 'TEST'},
            }]
            return {'data': {'questions': rows}}
        with patch('app.integrations.guard.request', new=AsyncMock(side_effect=request)):
            with self.factory() as db:
                self.assertEqual(asyncio.run(wb.sync_questions(db, 'owner')), 1)

    def page(self, **params):
        response = self.client.get('/questions/page', params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_wb_vin_is_visible_and_searchable(self):
        self.sync_wb()
        page = self.page()
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['items'][0]['text'], QUESTION)
        self.assertEqual(self.page(q=VIN)['total'], 1)

    def test_wb_resync_restores_previously_masked_question_without_duplicates(self):
        self.sync_wb()
        with self.factory() as db:
            row = db.scalar(select(Review).where(Review.marketplace == 'wb_question'))
            question_id = row.id
            row.text = mask_vin(QUESTION)
            db.commit()
        self.sync_wb()
        page = self.page()
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['items'][0]['id'], question_id)
        self.assertEqual(page['items'][0]['text'], QUESTION)

    def test_ozon_and_yandex_vin_is_visible_and_searchable(self):
        for provider, store_id in [('ozon', 'ozon-store'), ('yandex', 'yandex-store')]:
            with self.subTest(provider=provider):
                async def request(service, operation, **kwargs):
                    self.assertEqual(service, provider)
                    if provider == 'ozon':
                        return {'questions': [{'id': 'vin-question', 'sku': 1234,
                            'text': QUESTION, 'status': 'NEW',
                            'created_at': '2026-10-11T10:00:00Z'}]}
                    return {'result': {'questions': [{
                        'questionIdentifiers': {'id': 777, 'offerId': 'SKU-1'},
                        'text': QUESTION, 'createdAt': '2026-10-11T10:00:00Z'}]}}
                with patch('app.integrations.guard.request',
                           new=AsyncMock(side_effect=request)):
                    with self.factory() as db:
                        count = asyncio.run((ozon if provider == 'ozon' else yandex)
                                            .sync_questions(db, store_id))
                        self.assertEqual(count, 1)
                        set_setting(db, 'active_store:actor', store_id)
                        db.commit()
                page = self.page()
                self.assertEqual(page['total'], 1)
                self.assertEqual(page['items'][0]['marketplace'], provider + '_question')
                self.assertEqual(page['items'][0]['text'], QUESTION)
                self.assertEqual(self.page(q=VIN)['total'], 1)

    def test_questions_remain_scoped_to_active_store(self):
        self.sync_wb()
        with self.factory() as db:
            set_setting(db, 'active_store:actor', 'ozon-store')
            db.commit()
        self.assertEqual(self.page(q=VIN)['total'], 0)

    def test_questions_require_authentication(self):
        self.sync_wb()
        main.app.dependency_overrides.pop(main.authorize)
        self.assertEqual(self.client.get('/questions/page').status_code, 401)

    def test_public_reply_and_export_still_mask_vin(self):
        self.sync_wb()
        with self.assertRaisesRegex(ValueError, 'Полный VIN'):
            validate_reply('Здравствуйте! Ваш VIN ' + VIN + '.')
        response = self.client.get('/export/reviews')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(VIN, str(response.json()))
        self.assertEqual(response.json()[0]['text'], mask_vin(QUESTION))
