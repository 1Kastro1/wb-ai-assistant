"""Public catalog fixtures are anonymized; every database and HTTP request is isolated."""
import asyncio
import re
import unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
import httpx
from sqlalchemy import select
import test_fitment_catalog as fixtures
from app import internet_fitment as internet
from app.db import Review,Product,FitmentImport,Audit,Setting,set_setting
from app.services import generate_question_draft

VIN='JS2YA4150B6100001'
FIXTURES=Path(__file__).parent/'fixtures'


def document(name):return internet.Document((FIXTURES/name).read_text()).root


class InternetFitmentTests(unittest.TestCase):
    seed=fixtures.FitmentTests.seed
    upload=fixtures.FitmentTests.upload
    row=fixtures.FitmentTests.row
    oem_row=fixtures.FitmentTests.oem_row
    answer=fixtures.FitmentTests.answer

    def setUp(self):
        fixtures.FitmentTests.setUp(self)
        self.audit_patch=patch('app.internet_fitment.Session',self.factory);self.audit_patch.start()
        self.seed('Подойдет на автомобиль '+VIN)

    def tearDown(self):
        self.audit_patch.stop();fixtures.FitmentTests.tearDown(self)

    def research(self,force=False):
        with self.factory() as db:return asyncio.run(internet.research_question(db,db.get(Review,'fit-q'),db.get(Product,'100001'),force))

    def test_frame_skips_vin_only_decoder_and_is_not_persisted(self):
        with self.factory() as db:db.get(Review,'fit-q').text='AGH30-0000123';db.commit()
        with patch.object(internet.catalog,'decode',new=AsyncMock()) as decode, patch.object(internet.catalog,'lookup',new=AsyncMock(return_value={'status':'ACCESS_REQUIRED'})) as lookup:
            result=self.research()
            self.assertEqual(result['status'],'ACCESS_REQUIRED');decode.assert_not_called()
            lookup.assert_awaited_once_with('AGH30-0000123','oil_filter')
        with self.factory() as db:self.assertNotIn('0000123',str([vars(x) for x in db.scalars(select(Setting))]))

    def test_network_allowlist_rejects_arbitrary_hosts_paths_and_credentials(self):
        for url in ('http://partsouq.com/en/search/all','https://partsouq.com.evil.test/en/search/all','https://partsouq.com/en/user/auth','https://x@partsouq.com/en/search/all','https://127.0.0.1/en/search/all'):
            with self.assertRaises(ValueError):internet.safe_catalog_url(url)

    def test_refusal_redirect_and_challenge_are_not_bypassed_and_audit_has_no_vin(self):
        for code,content in ((403,'denied'),(302,''),(200,'verify you are human')):
            calls=[]
            async def handler(request):calls.append(str(request.url));return httpx.Response(code,text=content,headers={'location':'https://other.test/'})
            client=internet.PublicCatalog(httpx.MockTransport(handler))
            with self.assertRaisesRegex(ValueError,'ACCESS_REQUIRED'):asyncio.run(client.read('https://partsouq.com/en/search/all?q='+VIN))
            self.assertEqual(len(calls),1)
        with self.factory() as db:self.assertNotIn(VIN,str([vars(a) for a in db.scalars(select(Audit))]))

    def test_source_links_strip_vin_in_query_and_criteria(self):
        url='https://partsouq.com/en/partcatalog/groups/?carId=abc&q='+VIN+'&criteria=3e%2A'+VIN+'%21secret'
        clean=internet.source_url(url);self.assertNotIn(VIN,clean);self.assertNotIn('criteria',clean)
        with self.assertRaises(ValueError):internet.context_url(url,VIN,'different-car')

    def test_real_search_fixture_does_not_choose_first_of_six_vehicles(self):
        with patch.object(internet.catalog,'read',new=AsyncMock(return_value=document('partsouq-search.html'))) as read:
            result=asyncio.run(internet.catalog.lookup(VIN,'oil_filter'))
            self.assertEqual(result['status'],'MULTIPLE_VEHICLES');self.assertEqual(len(result['vehicles']),6);self.assertEqual(read.await_count,1)

    def test_real_diagram_distinguishes_filter_from_nozzle_and_other_parts(self):
        source=(FIXTURES/'partsouq-search.html').read_text();rows=re.findall(r'<tr\b[^>]*>.*?</tr>',source,re.S)
        search=internet.Document('<table>'+rows[0]+rows[1]+'</table>').root
        groups=document('partsouq-groups.html')
        def prune(node):
            node.children=[c for c in node.children if not isinstance(c,internet.Node) or c.tag!='article' or '115b - oil pan (k14c)' in c.text().lower()]
            for child in node.children:
                if isinstance(child,internet.Node):prune(child)
        prune(groups)
        async def read(url,*args):
            if '/search/all' in url:return search
            if '/diagram/' in url:return document('partsouq-diagram.html')
            return groups
        self.upload([self.oem_row()])
        with patch.object(internet.catalog,'read',side_effect=read),patch.object(internet.catalog,'decode',new=AsyncMock(return_value={})):
            result=self.research()
        self.assertEqual(result['status'],'FOUND',result);self.assertEqual(result['oem_candidates'][0]['oe_number'],'16510-82703');self.assertEqual(result['answer']['status'],'FIT')
        self.assertNotIn(VIN,str(result))
        with self.factory() as db:
            stored=str([row.value for row in db.scalars(select(Setting))])+str([row.rows for row in db.scalars(select(FitmentImport))]);self.assertNotIn(VIN,stored)

    def test_lookup_failure_keeps_partial_decode_separate_and_uses_cache(self):
        with patch.object(internet.catalog,'decode',new=AsyncMock(return_value={'make':'Suzuki','complete':False})),patch.object(internet.catalog,'lookup',new=AsyncMock(side_effect=ValueError('ACCESS_REQUIRED'))) as lookup:
            first=self.research();second=self.research(True)
            self.assertEqual(first['status'],'ACCESS_REQUIRED');self.assertFalse(first['vehicle']['complete']);self.assertTrue(second['cached']);self.assertEqual(lookup.await_count,1)
        with self.factory() as db:self.assertFalse(list(db.scalars(select(FitmentImport))))

    def test_auto_research_failure_does_not_fail_generation_or_claim_compatibility(self):
        with self.factory() as db:set_setting(db,'internet-fitment-enabled:owner',True);db.commit()
        with patch.object(internet.catalog,'decode',new=AsyncMock(return_value={})),patch.object(internet.catalog,'lookup',new=AsyncMock(side_effect=ValueError('UNAVAILABLE'))),patch('app.services.ollama.reply_variants',new=AsyncMock()) as model:
            with self.factory() as db:
                draft=asyncio.run(generate_question_draft(db,db.get(Review,'fit-q')));self.assertFalse(draft['quality']['passed']);self.assertEqual(draft['quality']['fitment']['internet']['status'],'UNAVAILABLE');model.assert_not_called()

    def test_any_category_has_uniqom_selection_without_unsupported_or_external_guess(self):
        with self.factory() as db:db.get(Product,'100001').name='Коврик';db.get(Product,'100001').category='Коврики';db.commit()
        with patch.object(internet.catalog,'lookup',new=AsyncMock()) as lookup:
            result=self.research()
            self.assertEqual(result['status'],'CATALOG_SELECTION')
            self.assertTrue(result['coverage']['all_categories'])
            self.assertEqual(result['provider'],'uniqom_browser')
            lookup.assert_not_called()

    def test_settings_are_owner_only_and_research_is_store_scoped(self):
        self.auth['role']='manager';self.assertEqual(self.client.post('/fitment/internet/settings',json={'enabled':True}).status_code,403)
        self.auth['role']='owner';self.assertEqual(self.client.post('/fitment/internet/settings',json={'enabled':True}).status_code,200)
        with self.factory() as db:db.get(Review,'fit-q').wb_account_id='other';db.commit()
        self.assertEqual(self.client.get('/fitment/questions/fit-q/research').status_code,404);self.assertEqual(self.client.post('/fitment/questions/fit-q/research',json={}).status_code,404)

    def test_owner_can_save_reviewed_partsouq_evidence_but_not_a_lookalike_host(self):
        body={'brand':'SUZUKI','part_number':'16510-82703','source':'https://partsouq.com/en/partcatalog/diagram/?carId=abc','fit':True}
        self.assertEqual(self.client.post('/fitment/questions/fit-q/evidence',json=body).status_code,200)
        body['source']='https://partsouq.com.attacker.test/en/partcatalog/diagram/'
        self.assertEqual(self.client.post('/fitment/questions/fit-q/evidence',json=body).status_code,400)


if __name__=='__main__':unittest.main()
