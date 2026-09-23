import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from llm_health import warn_unavailable_models

class StartupHealth(unittest.TestCase):
    def test_missing_model_warns_and_available_model_does_not(self):
        factory=MagicMock()
        factory.return_value.__enter__.return_value.models.list.return_value=SimpleNamespace(data=[SimpleNamespace(id='available')])
        with self.assertLogs('llm_health',level='WARNING') as logs:
            warn_unavailable_models('secret',{'GROQ_MODEL':'missing','GROQ_EXTRACT_MODEL':'available'},factory)
        self.assertEqual(len(logs.output),1)
        self.assertIn('GROQ_MODEL=missing',logs.output[0])
        self.assertNotIn('secret',logs.output[0])

    def test_api_failure_is_nonfatal_and_redacted(self):
        factory=MagicMock(side_effect=RuntimeError('failure gsk_private_test'))
        with self.assertLogs('llm_health',level='WARNING') as logs:
            warn_unavailable_models('gsk_private_test',{'GROQ_MODEL':'model'},factory)
        self.assertIn('RuntimeError',logs.output[0])
        self.assertNotIn('gsk_private_test',logs.output[0])

    def test_missing_key_does_not_make_request(self):
        factory=MagicMock()
        with self.assertLogs('llm_health',level='WARNING'):
            warn_unavailable_models('',{},factory)
        factory.assert_not_called()

if __name__=='__main__':unittest.main()
