import unittest
from unittest.mock import patch
from agents.sec_edgar import _merge_concepts_by_year, parse_financials


def concept(values):
    return {'units':{'USD':[{'form':'10-K','start':f'{y}-01-01','end':f'{y}-12-31','filed':f'{int(y)+1}-03-01','val':v} for y,v in values]}}

class RevenueConcepts(unittest.TestCase):
    def test_disjoint_years_merge_and_overlap_keeps_priority(self):
        gaap={'primary':concept([('2022',10),('2023',0)]),'secondary':concept([('2022',999),('2023',99),('2024',20)])}
        self.assertEqual(_merge_concepts_by_year(gaap,['primary','secondary']),[('2022',10.),('2023',0.),('2024',20.)])

    def test_empty_primary_does_not_hide_secondary(self):
        self.assertEqual(_merge_concepts_by_year({'secondary':concept([('2024',20)])},['primary','secondary']),[('2024',20.)])

    def test_parser_aligns_revenue_and_income_across_concept_change(self):
        gaap={'RevenueFromContractWithCustomerExcludingAssessedTax':concept([('2022',10e9)]),'Revenues':concept([('2023',20e9),('2024',30e9)]),'NetIncomeLoss':concept([('2022',1e9),('2023',2e9),('2024',3e9)])}
        with patch('agents.sec_edgar.fetch_company_metadata',return_value={}):
            result=parse_financials({'facts':{'us-gaap':gaap}},{'name':'Fixture'})
        self.assertEqual(result['years'],['2022','2023','2024'])
        self.assertEqual(result['revenue'],[10.,20.,30.])
        self.assertEqual(result['net_income'],[1.,2.,3.])

if __name__=='__main__':unittest.main()
