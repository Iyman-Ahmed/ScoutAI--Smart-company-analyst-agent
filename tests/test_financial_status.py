import unittest
from contextlib import ExitStack
from unittest.mock import patch
from agents import financial_analyst as f
from agents.synthesizer import _build_gaps_block
from financial_status import format_financial_status

class FinancialStatusTests(unittest.TestCase):
    def run_financial(self, qs=None, history=None, partial=None, edgar=True):
        filings = {'years':[2025], 'revenue':[0.7878], 'net_income':[0.1], 'source':'SEC EDGAR'} if edgar else {}
        with ExitStack() as stack:
            values = {'find_ticker':'UPWK','fetch_quote_summary':qs or {},'fetch_stock_history':history,
                      'fetch_quarterly_financials':{},'fetch_annual_financials':{},'fetch_recent_news':[],
                      '_fetch_financial_data_module':partial or {},'_build_raw_data_from_yf':{},
                      '_fetch_v7_quote':{},'_yf_get':None,'find_and_fetch_competitors':[]}
            for name,value in values.items():
                stack.enter_context(patch.object(f,name,return_value=value))
            stack.enter_context(patch('agents.sec_edgar.get_edgar_data',return_value=filings))
            stack.enter_context(patch.object(f.time,'sleep'))
            return f.get_financial_data('Upwork')

    def test_blocked_yahoo_keeps_edgar_and_discloses_failure(self):
        result = self.run_financial()
        sources = result['source_status']
        self.assertTrue(sources['yahoo_finance'].startswith('failed:'))
        self.assertEqual(sources['sec_edgar'],'ok')
        self.assertEqual(result['raw_data']['revenue_ttm'],'787.80M')
        status = format_financial_status(True,'UPWK',sources)
        self.assertIn('Yahoo Finance unavailable',status)
        self.assertIn('SEC filings only',status)
        self.assertNotIn('✅',status)
        self.assertIn('Yahoo Finance unavailable',result['combined_text'])
        self.assertIn('yahoo_finance',_build_gaps_block(sources,[]))
        print('BLOCKED YAHOO:',status)

    def test_stooq_is_not_counted_as_yahoo(self):
        result = self.run_financial(history={'source':'Stooq','dates':['2025-01-01'],'closes':[10]})
        self.assertTrue(result['source_status']['yahoo_finance'].startswith('failed:'))
        self.assertIn('Stooq',format_financial_status(True,'UPWK',result['source_status']))

    def test_history_only_is_partial_and_in_data_gaps(self):
        result = self.run_financial(history={'source':'Yahoo Finance','dates':['2025-01-01'],'closes':[10]})
        sources=result['source_status']
        self.assertTrue(sources['yahoo_finance'].startswith('partial:'))
        self.assertIn('partially available',format_financial_status(True,'UPWK',sources))
        self.assertIn('yahoo_finance',_build_gaps_block(sources,[]))

    def test_healthy_quote_retains_yahoo_source(self):
        result=self.run_financial(qs={'price':{'marketCap':{'raw':1e9,'fmt':'1B'}},'financialData':{'totalRevenue':{'raw':787800000,'fmt':'787.80M'}}})
        self.assertEqual(result['source_status']['yahoo_finance'],'ok')

    def test_partial_fundamentals_do_not_claim_full_success(self):
        result=self.run_financial(partial={'financialData':{'totalRevenue':{'raw':787800000,'fmt':'787.80M'}}})
        self.assertTrue(result['source_status']['yahoo_finance'].startswith('partial:'))

    def test_no_edgar_does_not_claim_sec_figures(self):
        result=self.run_financial(edgar=False)
        self.assertNotIn('SEC filings only',format_financial_status(True,'UPWK',result['source_status']))

    def test_empty_quote_modules_are_not_success(self):
        result=self.run_financial(qs={'price':{},'financialData':{}})
        self.assertTrue(result['source_status']['yahoo_finance'].startswith('failed:'))

    def test_dates_without_values_are_not_success(self):
        self.assertFalse(f._has_series_values({'years':[2025], 'revenue':[None]}))
        self.assertFalse(f._has_series_values({'revenue':{'dates':['2025-01-01'],'values':[]}}))
        self.assertTrue(f._has_series_values({'years':[2025], 'revenue':[0]}))
