import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.integrations import OzonProvider
from app.security import guard


class OzonConnectionTests(unittest.TestCase):
    def test_connection_uses_a_valid_bounded_review_page(self):
        async def request(service,operation,**kwargs):
            self.assertEqual((service,operation),('ozon','reviews'))
            self.assertGreaterEqual(kwargs['body']['limit'],20)
            self.assertLessEqual(kwargs['body']['limit'],100)
            self.assertEqual(kwargs['body']['sort_dir'],'DESC')
            self.assertEqual(kwargs['headers'],{'Client-Id':'123','Api-Key':'test-token'})
            return {'reviews':[]}
        store=SimpleNamespace(config={'client_id':'123'})
        with patch('app.integrations.store_credentials',return_value=(store,'test-token')),patch.object(guard,'request',side_effect=request) as probe:
            self.assertTrue(asyncio.run(OzonProvider().test(None,'shop')))
            self.assertEqual(probe.call_count,1)

    def test_real_api_errors_are_not_reported_as_success(self):
        store=SimpleNamespace(config={'client_id':'123'})
        with patch('app.integrations.store_credentials',return_value=(store,'test-token')),patch.object(guard,'request',new=AsyncMock(side_effect=ValueError('ozon: HTTP 403'))):
            with self.assertRaisesRegex(ValueError,'403'):
                asyncio.run(OzonProvider().test(None,'shop'))


if __name__=='__main__':unittest.main()
