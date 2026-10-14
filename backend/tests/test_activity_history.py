"""History checks use a disposable in-memory DB, including HTTP auditing."""
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import Request
from fastapi.testclient import TestClient
from app.db import Base,Activity,User,Memory,StoreProfile
from app.activity_history import activity_page,rule_details
from app import main


class ActivityHistoryTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(bind=self.engine,expire_on_commit=False)
        with self.factory() as db:
            db.add_all([User(id='owner',username='owner',display_name='Владелец',password_hash='unused',role='owner'),
                        User(id='colleague',username='colleague',display_name='Коллега',password_hash='unused',role='manager')])
            db.add(StoreProfile(id='owner',name='Мой магазин',provider='wb'))
            db.commit()
        self.actor={'role':'owner','user_id':'owner','username':'owner','permissions':['*'],'store_ids':['*']}
        self.previous_overrides=dict(main.app.dependency_overrides)
        def authorize(request: Request):
            request.state.actor={'user_id':self.actor['user_id'],'username':self.actor['username']}
            return self.actor
        def database():
            with self.factory() as db:
                yield db
        main.app.dependency_overrides[main.authorize]=authorize
        main.app.dependency_overrides[main.database]=database
        self.audit_patch=patch('app.main.Session',self.factory)
        self.audit_patch.start()
        # No lifespan/startup: migrations and production workers never run.
        self.client=TestClient(main.app,headers={'Origin':main.ORIGIN})

    def tearDown(self):
        self.client.close()
        self.audit_patch.stop()
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(self.previous_overrides)
        self.engine.dispose()

    def seed(self):
        with self.factory() as db:
            db.add_all([Activity(user_id='owner',username='owner',path='/memory/propose',method='POST',status=200,details=rule_details({'text':'Не используй слово «уважаемый»'},'rule_proposed')),
                        Activity(user_id='colleague',username='colleague',path='/drafts/d',method='PATCH',status=200),
                        Activity(user_id='owner',username='owner',path='/settings/schedule',method='PATCH',status=200)])
            db.commit()

    def test_owner_sees_team_with_human_names(self):
        self.seed()
        result=self.client.get('/activity/page').json()
        self.assertEqual(len(result['items']),3)
        self.assertEqual(result['items'][1]['actor_name'],'Коллега')

    def test_colleague_cannot_read_another_users_history(self):
        self.seed()
        self.actor={'role':'manager','user_id':'colleague','username':'colleague','permissions':[],'store_ids':[]}
        result=self.client.get('/activity/page').json()
        self.assertEqual([row['user_id'] for row in result['items']],['colleague'])
        self.assertEqual(self.client.get('/activity/page?user_id=owner').json()['items'],[])

    def test_cursor_pagination_never_repeats_rows(self):
        self.seed()
        first=self.client.get('/activity/page?limit=2').json()
        second=self.client.get('/activity/page?limit=2&before='+str(first['next_before'])).json()
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        self.assertEqual(len({row['id'] for row in first['items']+second['items']}),3)

    def test_search_finds_rule_text_without_treating_percent_as_wildcard(self):
        self.seed()
        self.assertEqual(len(self.client.get('/activity/page',params={'q':'уважаемый'}).json()['items']),1)
        self.assertEqual(self.client.get('/activity/page',params={'q':'%'}).json()['items'],[])

    def test_deleted_actor_falls_back_to_recorded_username(self):
        with self.factory() as db:
            db.add(Activity(user_id='former',username='former-user',path='/memory/propose',method='POST',status=200))
            db.commit()
            self.assertEqual(activity_page(db,self.actor)['items'][0]['actor_name'],'former-user')

    def test_details_only_copy_rule_fields(self):
        result=rule_details({'text':'x'*3000,'scope':'global','kind':'GLOBAL_RULE','password':'secret','api_key':'secret'},'rule_added')
        self.assertEqual(set(result),{'event','rule','scope','kind'})
        self.assertEqual(len(result['rule']),2000)

    def test_proposal_confirmation_and_deletion_record_exact_rule(self):
        text='Не используй слово «уважаемый»'
        response=self.client.post('/memory/propose',json={'text':text})
        self.assertEqual(response.status_code,200,response.text)
        response=self.client.post('/actions/'+response.json()['id']+'/confirm',json={'manual_ack':False})
        self.assertEqual(response.status_code,200,response.text)
        with self.factory() as db:
            memory=db.scalar(select(Memory))
            self.assertEqual(memory.text,text)
            mid=memory.id
        self.assertEqual(self.client.delete('/memory/'+mid).status_code,200)
        items=self.client.get('/activity/page').json()['items']
        self.assertEqual([row['details']['event'] for row in items],['rule_deleted','rule_added','rule_proposed'])
        self.assertTrue(all(row['details']['rule']==text for row in items))

    def test_rejected_validation_is_not_logged_as_success(self):
        self.assertEqual(self.client.post('/memory/propose',json={'text':'x'}).status_code,422)
        self.assertEqual(self.client.get('/activity/page').json()['items'],[])

    def test_rule_edit_and_rejection_keep_the_changed_text_and_scope(self):
        action=self.client.post('/memory/propose',json={'text':'Первое правило'}).json()
        response=self.client.patch('/actions/'+action['id']+'/memory',json={'text':'Не обещать компенсацию','scope':'brand:KANGAROO'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.post('/actions/'+action['id']+'/cancel').status_code,200)
        items=self.client.get('/activity/page').json()['items']
        self.assertEqual([item['details']['event'] for item in items[:2]],['rule_rejected','rule_updated'])
        self.assertTrue(all(item['details']['rule']=='Не обещать компенсацию' and item['details']['scope']=='brand:KANGAROO' for item in items[:2]))

    def test_rule_proposed_through_chat_is_also_recorded(self):
        response=self.client.post('/chat',json={'text':'Запомни: не используй слово «уважаемый»'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['data']['kind'],'memory')
        record=self.client.get('/activity/page').json()['items'][0]
        self.assertEqual(record['details']['event'],'rule_proposed')
        self.assertEqual(record['details']['rule'],'не используй слово «уважаемый»')


if __name__=='__main__':
    unittest.main()
