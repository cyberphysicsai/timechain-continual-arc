"""Synthetic protocol tests only. No ARC tasks or task solutions."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from timechain_arc.learner import TimechainLearner
from timechain_arc.programs import evaluate
from continual_arc.env import LearnerObservation, RequestDemos, Submit
from continual_arc.tasks import Pair
from continual_arc.env import DemosFeedback, SubmitFeedback


def observation(step=0, task='synthetic-a', **updates):
    data = dict(step=step,task_id=task,input=[[1,2],[3,4]],attempts_used=0,
                demo_requests_used=0,attempt_cap=8,demo_cap=8)
    data.update(updates)
    return LearnerObservation(**data)


class ScriptedClient:
    """Not a model; deterministic protocol-fixture response."""
    def __init__(self):
        self.contexts=[]
    def complete(self,messages):
        context=json.loads(messages[-1]['content']);self.contexts.append(context)
        return {'summary':'Synthetic fixture: reuse an identity mapping after a purchased example.',
                'perspectives':[{'name':'Fixture '+str(i),'summary':'Synthetic schema test, not a measured hypothesis.','score':240-i} for i in range(5)],
                'scores':dict(coherence=240,relevance=240,novelty=200,consistency=240,depth=220,covenant=255),
                'used_rings':[], 'task_note':'Synthetic identity fixture',
                'general_note':'Synthetic learned note', 'program':'def solve(g): return g',
                'action':None}


class IsolationTests(unittest.TestCase):
    def test_generated_policy_has_no_external_capabilities(self):
        obs={'input':[[1]]}
        for code in ('import os\ndef solve(g): return g',
                     'def solve(g): return open("secret").read()',
                     'def solve(g): return g.__class__.__bases__',
                     'from collections import __builtins__\ndef solve(g): return g'):
            result=evaluate(code,'grid',obs,{},[],[],2)
            self.assertIn('error',result,code)

    def test_demonstration_contradiction_is_rejected_and_timeout_bounded(self):
        result=evaluate('def solve(g): return [[0]]','grid',{'input':[[1]]},{},
                        [{'input':[[2]],'output':[[2]]}],[],2)
        self.assertIn('contradicts',result['error'])
        result=evaluate('def solve(g):\n while True: pass','grid',{'input':[[1]]},{},[],[],0.2)
        self.assertIn('timed out',result['error'])

    def test_runtime_analysis_can_add_new_computational_faculties(self):
        result=evaluate('def analyze(obs,demos): return {"height": len(obs["input"]), "evidence_pairs": len(demos)}',
                        'grid',{'input':[[1],[2]]},{},[],[],2)
        self.assertIsNone(result['action'])
        self.assertEqual({'height':2,'evidence_pairs':0},result['analysis'])

    def test_learning_commit_restart_and_full_reference_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            learner=TimechainLearner();learner.attach(Path(directory),{})
            learner.client=ScriptedClient();learner.begin_round(1)
            prefix=(learner.state_dir/'timechain/chain/rings.jsonl').read_bytes()
            obs=observation();action=learner.act(obs)
            self.assertIsInstance(action,Submit)
            self.assertEqual({},learner.memory['tasks'])
            self.assertEqual(prefix,(learner.state_dir/'timechain/chain/rings.jsonl').read_bytes())
            self.assertNotEqual(prefix,(learner.round_dir/'timechain/chain/rings.jsonl').read_bytes())
            learner.observe(obs,action,SubmitFeedback(correct=True,valid=True,failed=False,attempts_used=0))
            learner.end_round()
            saved=json.loads((learner.state_dir/'memory.json').read_text())
            self.assertIn('synthetic-a',saved['tasks'])
            restarted=TimechainLearner();restarted.attach(Path(directory),{})
            restarted.begin_round(1)
            # No client means reusing the learned program is the only way to pass.
            self.assertIsInstance(restarted.act(observation(1)),Submit)
            restarted.end_round()
            old_chain=(restarted.state_dir/'timechain/chain/rings.jsonl').read_text()
            self.assertIn('synthetic-a',old_chain)
            restarted.reset()
            self.assertEqual({'tasks':{},'general_note':''},restarted.memory)
            new_chain=(restarted.state_dir/'timechain/chain/rings.jsonl').read_text()
            self.assertNotIn('synthetic-a',new_chain)
            self.assertNotIn('Synthetic learned note',new_chain)
            self.assertTrue(json.loads((restarted.state_dir/'timechain/chain/auto_sprout.json').read_text())['enabled'])

    def test_no_manual_or_untracked_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            learner=TimechainLearner();learner.attach(Path(directory),{});learner.begin_round(1)
            with self.assertRaisesRegex(RuntimeError,'tracked model endpoint'):
                learner.act(observation())
            with self.assertRaisesRegex(RuntimeError,'width-1'):
                learner.begin_round(2)


if __name__=='__main__':
    unittest.main()
