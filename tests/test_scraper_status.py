import unittest
from unittest.mock import Mock, patch
from agents import web_scraper as w

class ScraperStatus(unittest.TestCase):
    def test_robots_metadata_and_product_text_are_not_challenges(self):
        html='<html><head><meta name="robots" content="index,follow"></head><body><main><h1>Robotics and Cloudflare integrations</h1><p>Products for robotics research teams.</p></main></body></html>'
        self.assertFalse(w._is_blocked(html))

    def test_actual_challenge_is_unavailable_without_browser_bypass(self):
        session=Mock();session.get.return_value=Mock(status_code=200,text='<title>Just a moment...</title><p>Verify you are human</p>')
        failures=[]
        with patch.object(w,'_try_playwright_fallback') as browser:
            self.assertIsNone(w._fetch_page('https://example.com',session,failures))
        browser.assert_not_called()
        self.assertEqual(failures,['blocked_challenge'])

    def test_http_denial_is_reported(self):
        session=Mock();session.get.return_value=Mock(status_code=403)
        failures=[]
        self.assertIsNone(w._fetch_page('https://example.com',session,failures))
        self.assertEqual(failures,['blocked_http_403'])
        status=w.format_scrape_status(0,'failed:'+failures[0])
        self.assertIn('site blocked automated access',status)
        self.assertNotIn('✅',status)

    def test_failed_scrape_has_empty_content_and_failed_status(self):
        session=Mock();session.get.return_value=Mock(status_code=403)
        with patch.object(w.cffi_requests,'Session',return_value=session),patch.object(w,'_ddg_find_website',return_value=None):
            result=w.scrape_website('https://example.com')
        self.assertEqual(result['pages_scraped'],0)
        self.assertEqual(result['combined_text'],'')
        self.assertEqual(result['source_status'],'failed:blocked_http_403')

    def test_empty_content_is_not_a_successful_page(self):
        session=Mock();session.get.return_value=Mock(status_code=200,text='<html><title>Shell</title><body></body></html>',url='https://example.com')
        with patch.object(w.cffi_requests,'Session',return_value=session),patch.object(w,'_try_playwright_fallback',return_value=None):
            result=w.scrape_website('https://example.com')
        self.assertEqual(result['pages_scraped'],0)
        self.assertEqual(result['source_status'],'failed:no_readable_content')

    def test_resolver_starts_at_root_instead_of_drivers_page(self):
        with patch("ddgs.DDGS") as ddgs:
            ddgs.return_value.text.return_value = [{"href":"https://www.nvidia.com/en-us/drivers?ref=search"}]
            self.assertEqual(w._ddg_find_website("Nvidia"), "https://www.nvidia.com")

    def test_success_has_positive_page_count(self):
        self.assertEqual(w.format_scrape_status(3,'ok'),'✅ Analyzed **3 pages**')

if __name__=='__main__':unittest.main()
