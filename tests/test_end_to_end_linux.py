"""Synthetic end-to-end check of the installed learner, proxy and actual bwrap.

The fixture returns a trivial identity program. No benchmark task or model API
is used, so this tests protocol integration, never benchmark performance.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from strict_host import StrictHostedLearner
from continual_arc_baselines.proxy import LLMProxy
from unittest.mock import patch
from go_bridge import GoBridge,CANONICAL,ALIAS
from continual_arc.env import LearnerObservation, Submit, SubmitFeedback
from continual_arc_baselines.tracking import Tracker


@unittest.skipUnless(sys.platform == 'linux', 'Requires official Linux host')
class InstalledLearnerTests(unittest.TestCase):
    def test_learning_and_frozen_restore_through_real_host_and_proxy(self):
        captured = []
        reply = {
            'summary': 'Synthetic protocol fixture: identity mapping only.',
            'perspectives': [{'name': 'Fixture ' + str(i),
                'summary': 'Synthetic fixture, not benchmark reasoning.', 'score': 240-i}
                for i in range(5)],
            'scores': dict(coherence=240,relevance=240,novelty=200,
                consistency=240,depth=220,covenant=255),
            'used_rings': [], 'task_note': 'Synthetic identity fixture',
            'general_note': None, 'program': 'def solve(g): return g', 'action': None,
        }

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                chunks = [
                    {'id': 'fixture', 'model': ALIAS, 'choices': [
                        {'index': 0, 'delta': {'content': json.dumps(reply)}, 'finish_reason': 'stop'}]},
                    {'id': 'fixture', 'model': ALIAS, 'choices': [], 'usage': {
                        'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13,
                        'cost':0,'opencode':{'billing_source':'go-plus'}}},
                ]
                data = (''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks)
                    + 'data: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        upstream = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        tracker = Tracker()
        scratch=tempfile.TemporaryDirectory(prefix='ct-go-receipts-')
        target=patch('go_bridge.URL','http://127.0.0.1:'+str(upstream.server_port)+'/chat/completions')
        target.start()
        bridge=GoBridge('synthetic-controller-key',Path(scratch.name)/'provider.jsonl')
        proxy = LLMProxy(bridge.start(),tracker=tracker,
            confined_to={CANONICAL:'max'},upstream_retries=0).start()
        try:
            with tempfile.TemporaryDirectory(prefix='ct-real-learner-') as temp:
                # No checkout or developer state is mounted. Import the wheel
                # installed in this interpreter's venv, as verification does.
                # Normalize '..' segments without resolving the venv's Python
                # symlink into the base runtime (which would lose its packages).
                python = os.path.abspath(os.environ.get('CT_TEST_LEARNER_PYTHON', sys.executable))
                host = StrictHostedLearner(python=python, runtime=[os.path.abspath(sys.base_prefix)],
                    learner='timechain_arc.learner:TimechainLearner', sandbox='bwrap', timeout_s=180)
                try:
                    host.attach(Path(temp)/'host', proxy.environ(), tracker)

                    def learn(step, task, value):
                        host.begin_round(1)
                        obs = LearnerObservation(step=step, task_id=task, input=[[value]],
                            attempts_used=0, demo_requests_used=0, attempt_cap=8, demo_cap=8)
                        action = host.act(obs)
                        self.assertIsInstance(action, Submit)
                        self.assertEqual([[value]], action.grid)
                        host.observe(obs, action, SubmitFeedback(correct=True, valid=True,
                            failed=False, attempts_used=0))
                        host.end_round()

                    learn(0, 'synthetic-stream', 1)
                    self.assertEqual({}, tracker.wait_ended(0, 5))
                    self.assertEqual(1, len(captured))
                    pid = host._proc.pid
                    host.freeze()
                    self.assertNotEqual(pid, host._proc.pid)
                    committed = host.state_dir/'learner/committed'
                    original = (committed/'timechain/chain/rings.jsonl').read_bytes()
                    self.assertTrue(json.loads((committed/'timechain/chain/auto_sprout.json').read_text())['enabled'])
                    # Learned source survives a process restart and solves a
                    # fresh synthetic input without any additional API call.
                    learn(1, 'synthetic-stream', 2)
                    self.assertEqual(1, len(captured))
                    learn(2, 'synthetic-test-only', 3)
                    self.assertEqual(2, len(captured))
                    self.assertNotEqual(original, (committed/'timechain/chain/rings.jsonl').read_bytes())
                    pid = host._proc.pid
                    host.reset()
                    self.assertNotEqual(pid, host._proc.pid)
                    self.assertEqual(original, (committed/'timechain/chain/rings.jsonl').read_bytes())
                    memory = json.loads((committed/'memory.json').read_text())
                    self.assertEqual(['synthetic-stream'], list(memory['tasks']))
                    self.assertEqual(0, host.resources()['model_decisions'])
                    self.assertEqual({}, tracker.wait_ended(0, 5))
                    self.assertEqual(2, tracker.usage.requests)
                    self.assertEqual(20, tracker.usage.input_tokens)
                    self.assertEqual(6, tracker.usage.output_tokens)
                    self.assertEqual(0, tracker.usage.unpriced_requests)
                    self.assertEqual(ALIAS, captured[0]['model'])
                    self.assertEqual('max', captured[0]['reasoning_effort'])
                    self.assertNotIn('provider', captured[0])
                finally:
                    host.close()
        finally:
            proxy.stop()
            bridge.close()
            target.stop()
            scratch.cleanup()
            upstream.shutdown()
            upstream.server_close()


if __name__ == '__main__':
    unittest.main()
