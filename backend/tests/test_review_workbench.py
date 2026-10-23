"""Selected jobs, autosave revisions and metrics use a disposable database only."""
import asyncio
import unittest
from unittest.mock import patch, AsyncMock
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import Request
from fastapi.testclient import TestClient
from app import main
from app.db import Base, User, StoreProfile, Product, Review, Draft, Job, Action, set_setting


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(bind=self.engine,expire_on_commit=False)
        with self.factory() as db:
            db.add_all([User(id='actor',username='ismailov.kamil',display_name='Камиль',role='owner',password_hash='unused'),
                        StoreProfile(id='owner',name='Магазин',provider='wb'),StoreProfile(id='other',name='Другой',provider='ozon'),
                        Product(id='p',name='Очиститель',brand='KANGAROO')]);db.flush()
            db.add_all([Review(id='r1',wb_review_id='1',product_id='p',rating=5,text='Хороший товар',wb_account_id='owner'),
                        Review(id='r2',wb_review_id='2',product_id='p',rating=2,text='Плохое качество',wb_account_id='owner'),
                        Review(id='existing',wb_review_id='3',product_id='p',rating=5,text='',wb_account_id='owner'),
                        Review(id='foreign',wb_review_id='4',product_id='p',rating=5,text='',wb_account_id='other'),
                        Review(id='question',wb_review_id='5',product_id='p',rating=5,text='Как применять?',marketplace='wb_question',wb_account_id='owner')])
            db.flush();db.add(Draft(id='d-existing',review_id='existing',text='Здравствуйте! Спасибо за Вашу оценку.',original='old',revision=3,quality={'passed':True,'timing':{'total_seconds':2.5,'route':'rules'}}))
            set_setting(db,'automation_schedule',{'auto_propose':True,'min_quality':70});db.commit()
        self.auth={'role':'owner','username':'ismailov.kamil','user_id':'actor','permissions':['*'],'store_ids':['*']}
        self.previous=dict(main.app.dependency_overrides)
        def database():
            with self.factory() as db:yield db
        def authorize(request:Request):
            request.state.actor={'user_id':self.auth['user_id'],'username':self.auth['username']};return self.auth
        main.app.dependency_overrides[main.database]=database;main.app.dependency_overrides[main.authorize]=authorize
        self.session_patch=patch('app.main.Session',self.factory);self.session_patch.start()
        self.queue_patch=patch('app.main.run_job_queue',new=AsyncMock());self.queue_patch.start()
        self.client=TestClient(main.app,headers={'Origin':main.ORIGIN})

    def tearDown(self):
        self.client.close();self.queue_patch.stop();self.session_patch.stop()
        main.app.dependency_overrides.clear();main.app.dependency_overrides.update(self.previous);self.engine.dispose()

    def submit(self,ids=('r1','r2')):
        response=self.client.post('/jobs/selected-drafts',json={'review_ids':list(ids)})
        self.assertEqual(response.status_code,200,response.text);return response.json()

    def test_page_query_count_is_bounded_and_historical_authors_are_preserved(self):
        from sqlalchemy import event
        from app.services import reviews_page
        with self.factory() as db:
            for i in range(30):
                pid='perf-product-'+str(i);rid='perf-review-'+str(i)
                db.add(Product(id=pid,name=pid));db.flush()
                db.add(Review(id=rid,wb_review_id=rid,product_id=pid,rating=5,text='ok',wb_account_id='owner'));db.flush()
                db.add(Draft(id='perf-draft-'+str(i),review_id=rid,text='Спасибо',original='Спасибо',created_by='actor',updated_by='deleted',edits=[{'by':'actor','old':'a','new':'b'}]))
            db.commit()
        statements=[]
        def count(conn,cursor,statement,parameters,context,executemany):
            if statement.lstrip().upper().startswith('SELECT'):statements.append(statement)
        event.listen(self.engine,'before_cursor_execute',count)
        try:
            with self.factory() as db:
                result=reviews_page(db,account_id='owner',q='perf-product',limit=30)
                self.assertEqual(result['total'],30)
                self.assertLessEqual(len(statements),6)  # One shared query checks current owner rules.
                for item in result['items']:
                    self.assertEqual(item['draft']['created_by_name'],'Камиль')
                    self.assertEqual(item['draft']['updated_by_name'],'Бывший сотрудник')
                    self.assertEqual(item['draft']['edits'][0]['by_name'],'Камиль')
        finally:event.remove(self.engine,'before_cursor_execute',count)

    def test_filter_totals_match_original_predicates_with_one_query(self):
        from sqlalchemy import event
        from app.services import _review_query,review_filter_counts
        with self.factory() as db:
            db.get(Review,'r2').manual=True
            db.get(Review,'r1').text=' \n\t'
            db.commit()
            expected={view:db.scalar(select(func.count()).select_from(_review_query({'view':view,'account_id':'owner'}).subquery())) for view in ('all','positive','negative','rating_only','with_text','manual')}
            statements=[]
            def count(*args):statements.append(args[2])
            event.listen(self.engine,'before_cursor_execute',count)
            try:
                self.assertEqual(review_filter_counts(db,account_id='owner'),expected)
                self.assertEqual(len(statements),1)
                self.assertEqual(review_filter_counts(db,q='not-found',account_id='owner'),dict.fromkeys(expected,0))
            finally:event.remove(self.engine,'before_cursor_execute',count)

    async def generate(self,db,rid,instruction=''):
        draft=Draft(id='d-'+rid,review_id=rid,text='Здравствуйте! Спасибо за Ваш отзыв.',original='original',quality={'passed':True,'score':100,'timing':{'total_seconds':1,'route':'rules'}})
        db.add(draft);db.commit();return main.public(draft)

    def run_job(self,job,generate=None):
        with patch('app.main.generate_draft',generate or self.generate):asyncio.run(main.run_sync_job(job['id']))
        return self.client.get('/jobs/'+job['id']).json()

    def test_selected_queue_excludes_existing_drafts_and_duplicates(self):
        job=self.submit(['r1','existing','r1'])
        self.assertEqual(job['result']['selected_ids'],['r1']);self.assertEqual(job['result']['skipped'],1)
        same=self.submit(['r1','existing']);self.assertEqual(same['id'],job['id'])
        with self.factory() as db:
            self.assertEqual(db.get(Draft,'d-existing').revision,3)
            self.assertEqual(db.scalar(select(func.count()).select_from(Job)),1)

    def test_check_now_processes_entire_store_without_reusing_limited_jobs(self):
        partial = self.submit(['r1'])
        with self.factory() as db:
            db.add(Job(id='limited-date', kind='drafts', result={'account_id':'owner','since':'2099-01-01'}))
            for i in range(105):
                db.add(Review(id='all-'+str(i), wb_review_id='all-'+str(i), product_id='p',
                              rating=1 if i % 2 else 5, text='' if i % 2 else 'Отзыв',
                              created_at='2020-01-01T00:00:00+00:00', wb_account_id='owner'))
            db.add(Review(id='answered',wb_review_id='answered',product_id='p',rating=5,text='',wb_account_id='owner',is_answered=True))
            db.commit()
        response = self.client.post('/jobs/drafts', json={'manual_start':True})
        self.assertEqual(response.status_code,200,response.text)
        job = response.json()
        self.assertNotIn(job['id'],[partial['id'],'limited-date'])
        self.assertEqual(job['result']['total'],107)
        self.assertTrue(job['result']['manual_batch'])
        self.assertEqual(self.client.post('/jobs/drafts',json={'manual_start':True}).json()['id'],job['id'])
        result = self.run_job(job)
        self.assertEqual(result['result']['processed'],107)
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Draft)),108)
            self.assertEqual(db.get(Draft,'d-existing').revision,3)
            for rid in ['foreign','question','answered']:
                self.assertIsNone(db.scalar(select(Draft).where(Draft.review_id==rid)))
            self.assertEqual(db.scalar(select(func.count()).select_from(Action)),0)

    def test_check_now_requires_enabled_assistant(self):
        with self.factory() as db:
            set_setting(db,'assistant_enabled',False);db.commit()
        self.assertEqual(self.client.post('/jobs/drafts',json={'manual_start':True}).status_code,400)

    def test_foreign_unknown_or_question_ids_reject_entire_request(self):
        for rid in ['foreign','unknown','question']:
            self.assertEqual(self.client.post('/jobs/selected-drafts',json={'review_ids':['r1',rid]}).status_code,404)
        with self.factory() as db:self.assertEqual(db.scalar(select(func.count()).select_from(Job)),0)

    def test_no_eligible_reviews_and_too_large_selection_are_readable(self):
        self.assertEqual(self.client.post('/jobs/selected-drafts',json={'review_ids':['existing']}).status_code,409)
        self.assertEqual(self.client.post('/jobs/selected-drafts',json={'review_ids':['r1']*101}).status_code,422)

    def test_batch_creates_only_selected_drafts_and_never_proposes_publication(self):
        job=self.submit(['r1']);result=self.run_job(job)
        self.assertEqual(result['status'],'completed');self.assertEqual(result['result']['processed'],1)
        self.assertIn('timing',result['result']);self.assertGreaterEqual(result['result']['timing']['queue_seconds'],0)
        with self.factory() as db:
            self.assertIsNotNone(db.get(Draft,'d-r1'));self.assertIsNone(db.get(Draft,'d-r2'))
            self.assertEqual(db.scalar(select(func.count()).select_from(Action)),0)
            self.assertEqual(db.get(Draft,'d-r1').created_by,'actor')

    def test_progress_keeps_successful_drafts_and_specific_failure(self):
        async def partial(db,rid,instruction=''):
            if rid=='r2':raise ValueError('Нет подтверждённой инструкции')
            return await self.generate(db,rid,instruction)
        result=self.run_job(self.submit(),partial)
        self.assertEqual(result['result']['total'],2);self.assertEqual(result['result']['processed'],1)
        self.assertEqual(result['result']['failed'],1)
        self.assertEqual(result['result']['failures'][0]['review_id'],'r2')

    def test_cancel_keeps_already_prepared_draft(self):
        job=self.submit()
        async def cancel_after_one(db,rid,instruction=''):
            result=await self.generate(db,rid,instruction)
            db.get(Job,job['id']).cancel_requested=True;db.commit();return result
        result=self.run_job(job,cancel_after_one)
        self.assertEqual(result['status'],'cancelled')
        with self.factory() as db:self.assertEqual(db.scalar(select(func.count()).select_from(Draft)),2)

    def test_auto_pause_does_not_cancel_manual_selection(self):
        job=self.submit();self.assertEqual(self.client.post('/auto-replies/pause').status_code,200)
        self.assertEqual(self.client.get('/jobs/'+job['id']).json()['status'],'queued')

    def test_stale_revision_returns_conflict_without_overwriting(self):
        response=self.client.patch('/drafts/d-existing',json={'text':'Здравствуйте! Мой новый ответ.', 'revision':2})
        self.assertEqual(response.status_code,409)
        with self.factory() as db:self.assertEqual(db.get(Draft,'d-existing').text,'Здравствуйте! Спасибо за Вашу оценку.')

    def test_autosave_returns_new_revision_and_preserves_generation_timing(self):
        response=self.client.patch('/drafts/d-existing',json={'text':'Здравствуйте! Благодарим за высокую оценку товара.', 'revision':3})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['revision'],4)
        self.assertEqual(response.json()['quality']['timing']['total_seconds'],2.5)
        self.assertEqual(self.client.patch('/drafts/d-existing',json={'text':'Здравствуйте! Старый вариант.', 'revision':3}).status_code,409)

    def test_metrics_are_scoped_and_do_not_expose_prompts_or_buyer_text(self):
        self.run_job(self.submit(['r1']))
        with self.factory() as db:
            db.add(Job(id='foreign-job',kind='drafts',status='failed',result={'account_id':'other','prompt':'secret','timing':{'queue_seconds':999}}));db.commit()
        response=self.client.get('/generation/metrics');self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['failed_jobs'],0);self.assertEqual(response.json()['measured_drafts'],2)
        self.assertNotIn('secret',response.text);self.assertNotIn('Хороший товар',response.text)

    def test_request_identity_recovers_completed_batch_without_regeneration(self):
        payload={'review_ids':['r1'],'operation_key':'request-key-123'}
        first=self.client.post('/jobs/selected-drafts',json=payload).json()
        self.run_job(first)
        recovered=self.client.post('/jobs/selected-drafts',json=payload)
        self.assertEqual(recovered.status_code,200,recovered.text)
        self.assertEqual(recovered.json()['id'],first['id'])
        self.assertEqual(recovered.json()['status'],'completed')
        with self.factory() as db:self.assertEqual(db.scalar(select(func.count()).select_from(Job)),1)

    def test_permissions_and_disabled_assistant_are_enforced(self):
        self.auth.update(role='member',permissions=['data:read'])
        self.assertEqual(self.client.post('/jobs/selected-drafts',json={'review_ids':['r1']}).status_code,403)
        self.assertEqual(self.client.get('/generation/metrics').status_code,403)
        self.auth.update(role='owner',permissions=['*'])
        with self.factory() as db:set_setting(db,'assistant_enabled',False);db.commit()
        self.assertEqual(self.client.post('/jobs/selected-drafts',json={'review_ids':['r1']}).status_code,400)

    def test_question_generation_records_timing_and_model_unavailability(self):
        with self.factory() as db:
            with patch('app.services.ollama.reply_variants',new=AsyncMock(side_effect=ValueError('Offline'))),patch('app.services.web_research.search_instructions',new=AsyncMock(return_value=[])):
                result=asyncio.run(main.generate_draft(db,'question'))
            self.assertEqual(result['quality']['timing']['route'],'fallback')
            self.assertEqual(result['quality']['timing']['fallback_reason'],'unavailable')
            self.assertGreaterEqual(result['quality']['timing']['total_seconds'],0)


if __name__=='__main__':unittest.main()
