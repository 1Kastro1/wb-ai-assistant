import unittest
from app.product_images import card_image_url


class ProductImageTests(unittest.TestCase):
    def test_prefers_small_card_photo(self):
        self.assertEqual(card_image_url({'photos':[{'big':'https://example.com/big.webp','c246x328':'https://example.com/small.webp'}]}),'https://example.com/small.webp')

    def test_missing_and_malformed_photos_use_placeholder(self):
        for card in ({},{'photos':None},{'photos':'bad'},{'photos':[None,3,{}]}):
            self.assertEqual(card_image_url(card),'')

    def test_rejects_insecure_and_credential_urls(self):
        for url in ('javascript:alert(1)','http://example.com/a','https://user:password@example.com/a','https://[bad'):
            self.assertEqual(card_image_url({'photos':[{'big':url}]}),'')

    def test_uses_next_valid_photo(self):
        self.assertEqual(card_image_url({'photos':[{}, {'square':'https://example.com/1.webp'}]}),'https://example.com/1.webp')


if __name__=='__main__':
    unittest.main()
