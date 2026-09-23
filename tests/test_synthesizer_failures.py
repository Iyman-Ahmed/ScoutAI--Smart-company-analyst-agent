import os
import unittest
from unittest.mock import patch
from agents import synthesizer as s

class SynthesisFailures(unittest.TestCase):
    def test_all_failed_rungs_are_logged_without_credentials(self):
        secret = 'gsk_test_private_value'
        with patch.dict(os.environ, {'CEREBRAS_API_KEY': 'cerebras-private-value'}), \
             patch.object(s, '_groq_call', side_effect=ValueError('404 model_not_found ' + secret)), \
             patch.object(s, '_cerebras_call', side_effect=RuntimeError('401 cerebras-private-value')), \
             self.assertLogs(s.logger, level='WARNING') as captured:
            result=s.synthesize_report('Test','https://example.com','Public website','Research','Revenue',secret)
        logs='\n'.join(captured.output)
        self.assertEqual(len(captured.output), 3)
        self.assertIn('ValueError: 404 model_not_found',logs)
        self.assertIn('RuntimeError: 401',logs)
        self.assertNotIn(secret,logs)
        self.assertNotIn('cerebras-private-value',logs)
        self.assertIn('deterministic fallback',result)

    def test_missing_keys_have_explicit_diagnostics(self):
        with patch.dict(os.environ, {'GROQ_API_KEY':'','CEREBRAS_API_KEY':''}), \
             self.assertLogs(s.logger, level='WARNING') as captured:
            s.synthesize_report('Test','https://example.com','Website','Research','Revenue','')
        logs='\n'.join(captured.output)
        self.assertIn('RuntimeError: no GROQ_API_KEY',logs)
        self.assertIn('RuntimeError: no CEREBRAS_API_KEY',logs)

    def test_successful_primary_does_not_invoke_fallback(self):
        with patch.object(s,'_groq_call',side_effect=['Briefing','LLM report']), \
             patch.object(s,'_cerebras_call') as fallback:
            result=s.synthesize_report('Test','https://example.com','Website','Research','Revenue','key')
        self.assertEqual(result,'LLM report')
        fallback.assert_not_called()

    def test_reasoning_output_budget_and_truncation(self):
        from types import SimpleNamespace
        with patch('langchain_groq.ChatGroq') as client:
            client.return_value.invoke.return_value = SimpleNamespace(content='Complete report', response_metadata={'finish_reason':'stop'})
            self.assertEqual(s._groq_call('openai/gpt-oss-120b','system','human','key'), 'Complete report')
            self.assertEqual(client.call_args.kwargs['reasoning_effort'], 'low')
            self.assertEqual(client.call_args.kwargs['reasoning_format'], 'hidden')
            self.assertEqual(client.call_args.kwargs['max_tokens'], 4096)
            client.return_value.invoke.return_value.response_metadata = {'finish_reason':'length'}
            with self.assertRaisesRegex(RuntimeError, 'truncated'):
                s._groq_call('openai/gpt-oss-120b','system','human','key')
            client.return_value.invoke.return_value.response_metadata = {'finish_reason':'stop'}
            s._groq_call('custom-model','system','human','key')
            self.assertNotIn('reasoning_effort', client.call_args.kwargs)

    def test_bearer_token_redacted(self):
        with self.assertLogs(s.logger,level='WARNING') as captured:
            s._log_llm_failure('test',RuntimeError('Authorization: Bearer unknown-secret'))
        self.assertNotIn('unknown-secret','\n'.join(captured.output))

if __name__=='__main__':unittest.main()
