"""No network or model calls: native translation and rejected-program regression."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from openai_cli import NativeOpenAIProxy
from test_isolation import ScriptedClient, observation
from timechain_arc.learner import TimechainLearner


class GPTProfileTests(unittest.TestCase):
    @unittest.skipUnless(TimechainLearner().config['model']=='openai/gpt-6.1-sol', 'Native GPT profile is inactive in a DeepSeek release')
    def test_native_translation_keeps_stream_usage_and_does_not_invent_costs(self):
        proxy=NativeOpenAIProxy('http://127.0.0.1:9',
            confined_to={'openai/gpt-6.1-sol':'xhigh'})
        try:
            request={'model':'openai/gpt-6.1-sol','messages':[{'role':'user','content':'fixture'}],
                'max_tokens':100,'reasoning':{'effort':'xhigh'},'stream':True,
                'session_id':'fixture','provider':{'order':['fixture']}}
            _,_,body=proxy.prepare(path='/v1/chat/completions',headers={'Content-Type':'application/json'},body=json.dumps(request).encode())
            self.assertEqual('gpt-6.1-sol',body['model'])
            self.assertEqual('xhigh',body['reasoning_effort'])
            self.assertEqual(65536,body['max_completion_tokens'])
            self.assertFalse(body['store'])
            self.assertTrue(body['stream_options']['include_usage'])
            self.assertEqual('default',body['service_tier'])
            for key in ('reasoning','max_tokens','provider','usage','session_id'):
                self.assertNotIn(key,body)
            self.assertEqual(0,proxy.upstream_retries)
            self.assertEqual(0,proxy.credit_wait_attempts)
        finally:
            if proxy._client is not None:proxy._client.close()

    def test_blocked_program_is_visible_and_null_retry_is_rejected(self):
        learner=TimechainLearner();learner.chain=Mock();learner._check_code=Mock()
        learner.client=ScriptedClient()
        original=learner.client.complete
        def blocked(messages):
            reply=original(messages);reply['program']=None
            return reply
        learner.client.complete=blocked
        obs=observation();w=learner._working(obs)
        w['program']='def solve(g): return g';w['program_blocked']=True
        with self.assertRaisesRegex(ValueError,'blocked'):
            learner._model(w,obs,{'evidence':{'recalled':[]}})
        self.assertTrue(learner.client.contexts[-1]['program_blocked'])


if __name__=='__main__':unittest.main()
