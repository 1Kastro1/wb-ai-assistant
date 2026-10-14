"""Disposable database only: register, decide, login, revoke and preserve history."""
import unittest
import asyncio
from unittest.mock import patch
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import Request
from fastapi.testclient import TestClient
from argon2 import PasswordHasher
from app import main
from app.db import Base, User, RegistrationRequest, LoginSession, Activity, StoreProfile, set_setting


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(bind=self.engine,expire_on_commit=False)
        with self.factory() as db:
            db.add(User(id='approver',username='ismailov.kamil',display_name='Камиль',role='owner',password_hash='unused',enabled=True))
            db.add_all([StoreProfile(id='wb-store',name='WB',provider='wb'),StoreProfile(id='ozon-store',name='Ozon',provider='ozon')])
            db.commit()
        self.auth={'role':'owner','username':'ismailov.kamil','user_id':'approver','permissions':['*'],'store_ids':['*']}
        self.previous=dict(main.app.dependency_overrides)
        def database():
            with self.factory() as db: yield db
        def authorize(request:Request):
            request.state.actor={'user_id':self.auth['user_id'],'username':self.auth['username']}
            return self.auth
        main.app.dependency_overrides[main.database]=database
        main.app.dependency_overrides[main.authorize]=authorize
        self.patch=patch('app.main.Session',self.factory);self.patch.start()
        main.registration_attempts.clear();main.login_attempts.clear()
        # No lifespan; production migrations and workers are not started.
        self.client=TestClient(main.app,headers={'Origin':main.ORIGIN})

    def tearDown(self):
        self.client.close();self.patch.stop()
        main.app.dependency_overrides.clear();main.app.dependency_overrides.update(self.previous)
        self.engine.dispose()

    def register(self):
        response=self.client.post('/auth/register',json={'username':' New.User ','password':'strong-password-123'})
        self.assertEqual(response.status_code,200,response.text)
        self.receipt=response.json()['receipt']
        self.request_id=self.client.get('/registration-requests').json()[0]['id']
        return response

    def decide(self,decision='accept',position='Менеджер WB'):
        return self.client.post('/registration-requests/'+self.request_id+'/decision',json={'decision':decision,'position':position})

    def test_registration_only_two_fields_creates_no_session_or_user(self):
        response=self.register()
        self.assertNotIn('wb_session',response.cookies)
        with self.factory() as db:
            self.assertIsNone(db.scalar(select(User).where(User.username=='new.user')))
            item=db.get(RegistrationRequest,self.request_id)
            self.assertNotEqual(item.password_hash,'strong-password-123')
            self.assertTrue(PasswordHasher().verify(item.password_hash,'strong-password-123'))
            self.assertEqual(db.scalar(select(func.count()).select_from(LoginSession)),0)

    def test_pending_login_does_not_open_access(self):
        self.register()
        response=self.client.post('/auth/login',json={'username':'new.user','password':'strong-password-123'})
        self.assertEqual(response.status_code,403)
        self.assertNotIn('wb_session',response.cookies)
        self.assertEqual(self.client.post('/auth/login',json={'username':'new.user','password':'wrong'}).status_code,401)

    def test_only_designated_owner_sees_and_decides_requests(self):
        self.register()
        for role,username in [('owner','other.owner'),('member','ismailov.kamil')]:
            self.auth.update(role=role,username=username)
            self.assertEqual(self.client.get('/registration-requests').status_code,403)
            self.assertEqual(self.decide().status_code,403)

    def test_accept_allows_login_with_original_password_and_scoped_access(self):
        self.register();self.assertEqual(self.decide().status_code,200)
        response=self.client.post('/auth/login',json={'username':'new.user','password':'strong-password-123'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('wb_session',response.cookies)
        self.assertEqual(response.json()['user']['role'],'member')
        self.assertEqual(response.json()['user']['store_ids'],['wb-store'])
        self.assertNotIn('*',response.json()['user']['permissions'])
        with self.factory() as db:self.assertEqual(db.get(RegistrationRequest,self.request_id).password_hash,'')
        self.assertNotIn('password_hash',str(self.client.get('/users').json()))

    def test_rejection_erases_hash_no_account_and_private_receipt_reports_status(self):
        self.register();self.assertEqual(self.decide('reject').status_code,200)
        response=self.client.post('/auth/registration/status',json={'receipt':self.receipt})
        self.assertEqual(response.json()['status'],'rejected')
        self.assertEqual(self.client.post('/auth/registration/status',json={'receipt':'x'*43}).status_code,404)
        with self.factory() as db:
            self.assertEqual(db.get(RegistrationRequest,self.request_id).password_hash,'')
            self.assertIsNone(db.scalar(select(User).where(User.username=='new.user')))

    def test_duplicates_and_double_decisions_are_conflicts(self):
        self.register()
        self.assertEqual(self.client.post('/auth/register',json={'username':'NEW.USER','password':'other-password-123'}).status_code,409)
        self.assertEqual(self.decide().status_code,200)
        self.assertEqual(self.decide('reject').status_code,409)

    def test_delete_removes_sessions_and_account_preserves_history(self):
        self.register();self.decide()
        self.client.post('/auth/login',json={'username':'new.user','password':'strong-password-123'})
        with self.factory() as db:
            user=db.scalar(select(User).where(User.username=='new.user'));uid=user.id
            db.add(Activity(user_id=uid,username=user.username,method='PATCH',path='/drafts/d',status=200));db.commit()
        self.assertEqual(self.client.delete('/users/'+uid).status_code,200)
        with self.factory() as db:
            self.assertIsNone(db.get(User,uid))
            self.assertEqual(db.scalar(select(func.count()).select_from(LoginSession).where(LoginSession.user_id==uid)),0)
            self.assertIsNotNone(db.scalar(select(Activity).where(Activity.user_id==uid)))
            self.assertEqual(db.get(RegistrationRequest,self.request_id).status,'deleted')
        self.assertEqual(self.client.post('/auth/login',json={'username':'new.user','password':'strong-password-123'}).status_code,401)

    def test_owner_and_self_cannot_be_deleted_or_member_manage_users(self):
        self.assertEqual(self.client.delete('/users/approver').status_code,409)
        self.auth.update(role='member')
        self.assertEqual(self.client.delete('/users/approver').status_code,403)
        self.assertEqual(self.client.get('/users').status_code,403)

    def test_secrets_never_exposed_in_requests_status_or_audit(self):
        self.register();self.decide()
        with self.factory() as db:
            events=list(db.scalars(select(Activity)))
            self.assertTrue(any(e.details.get('event')=='registration_accepted' for e in events))
            for event in events:
                self.assertNotIn('password',str(event.details));self.assertNotIn(self.receipt,str(event.details))
        self.assertEqual(self.client.get('/registration-requests').json(),[])

    def test_rate_limit_and_validation(self):
        self.assertEqual(self.client.post('/auth/register',json={'username':'a  ','password':'short'}).status_code,422)
        self.assertEqual(self.client.post('/auth/register',json={'username':'a  ','password':'strong-password-123'}).status_code,400)
        main.registration_attempts['testclient']=[main.time.time()]*20
        self.assertEqual(self.client.post('/auth/register',json={'username':'valid','password':'strong-password-123'}).status_code,429)

    def test_session_cookie_of_deleted_user_is_rejected(self):
        self.register();self.decide()
        response=self.client.post('/auth/login',json={'username':'new.user','password':'strong-password-123'})
        token=response.cookies['wb_session']
        with self.factory() as db:uid=db.scalar(select(User.id).where(User.username=='new.user'))
        self.client.delete('/users/'+uid)
        # Exercise real authorization instead of the owner test override.
        main.app.dependency_overrides.pop(main.authorize)
        self.client.cookies.set('wb_session',token)
        self.assertEqual(self.client.get('/auth/session').status_code,401)
        self.assertEqual(self.client.get('/registration-requests').status_code,401)

    def restart_application(self):
        async def idle_scheduler():
            await asyncio.Future()
        async def restart():
            # Real startup against the disposable DB; migrations are mocked.
            with patch('alembic.command.upgrade'), patch('app.main.scheduler_loop',idle_scheduler):
                async with main.lifespan(main.app):
                    pass
        asyncio.run(restart())

    def test_restart_does_not_recreate_removed_legacy_owner(self):
        with self.factory() as db:
            set_setting(db,'password_hash','legacy-hash');db.commit()
        self.restart_application();self.restart_application()
        with self.factory() as db:
            self.assertIsNone(db.get(User,'owner'))
            self.assertEqual(db.get(User,'approver').username,'ismailov.kamil')
            self.assertEqual(db.scalar(select(func.count()).select_from(User)),1)

    def test_legacy_owner_still_migrates_when_no_accounts_exist(self):
        with self.factory() as db:
            db.delete(db.get(User,'approver'));set_setting(db,'password_hash','legacy-hash');db.commit()
        self.restart_application()
        with self.factory() as db:
            self.assertEqual(db.get(User,'owner').password_hash,'legacy-hash')


if __name__=='__main__':unittest.main()
