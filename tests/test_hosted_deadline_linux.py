"""Real host/proxy regression: a slow synthetic case fails, the next runs."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from go_bridge import ALIAS, CANONICAL, GoBridge
from strict_host import StrictHostedLearner
from continual_arc.env import EnvConfig
from continual_arc.runner import run
from continual_arc.schedule import Schedule, Slot
from continual_arc.tasks import TaskRegistry
from continual_arc.testing import FakeSource
from continual_arc_baselines.proxy import LLMProxy
from continual_arc_baselines.tracking import TrackedLearner, Tracker


@unittest.skipUnless(sys.platform == 'linux', 'Requires real Linux bwrap host')
class HostedDeadlineTests(unittest.TestCase):
    def test_exhausted_case_is_scored_and_next_case_continues_with_paired_receipts(self):
        captured = []
        stopping = threading.Event()
        reply = {
            'summary': 'Synthetic identity protocol fixture only.',
            'perspectives': [{'name': 'Fixture ' + str(i), 'summary': 'No benchmark task.',
                              'score': 240-i} for i in range(5)],
            'scores': dict(coherence=240, relevance=240, novelty=200,
                           consistency=240, depth=220, covenant=255),
            'used_rings': [], 'task_note': 'Synthetic identity fixture',
            'general_note': None, 'program': 'def solve(g): return g', 'action': None,
        }

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                captured.append(body)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    if len(captured) == 1:
                        heartbeat = ('data: ' + json.dumps({'model': ALIAS, 'choices': [
                            {'index': 0, 'delta': {'reasoning': 'synthetic heartbeat'},
                             'finish_reason': None}]}) + '\n\n').encode()
                        while not stopping.is_set():
                            self.wfile.write(heartbeat)
                            self.wfile.flush()
                            time.sleep(0.01)
                    else:
                        chunks = [
                            {'model': ALIAS, 'choices': [{'index': 0,
                             'delta': {'content': json.dumps(reply)}, 'finish_reason': 'stop'}]},
                            {'model': ALIAS, 'choices': [], 'usage': {
                                'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13,
                                'cost': 0, 'opencode': {'billing_source': 'go-plus'}}},
                        ]
                        data = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks)
                        self.wfile.write((data + 'data: [DONE]\n\n').encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        class IdentitySource(FakeSource):
            def shift(self, task_id):
                return 0

        upstream = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        package = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='ct-hosted-deadline-') as directory:
            root = Path(directory)
            tracker = Tracker()
            with patch('go_bridge.URL', 'http://127.0.0.1:' + str(upstream.server_port) + '/chat/completions'):
                bridge = GoBridge('synthetic-controller-key', root / 'provider.jsonl')
                proxy = LLMProxy(bridge.start(), tracker=tracker,
                    confined_to={CANONICAL: 'max'}, upstream_retries=0).start()
                host = StrictHostedLearner(python=os.path.abspath(sys.executable),
                    learner='host_fixture:DeadlineFixtureLearner', code=str(package / 'tests'),
                    runtime=[os.path.abspath(sys.base_prefix)], sandbox='bwrap', timeout_s=30,
                    reset_reference=True)
                try:
                    host.attach(root / 'system', proxy.environ(), tracker)
                    schedule = Schedule(name='synthetic-deadline', seed=0,
                        slots=[Slot('fake000', 1, 'stream'), Slot('fake001', 2, 'stream')],
                        sources={'fake000': 'fake', 'fake001': 'fake'}).reset_each()
                    registry = TaskRegistry({'fake': IdentitySource(n_tasks=2, size=(1, 1))})
                    registry.assign_many(schedule.sources)
                    run(TrackedLearner(host, tracker), schedule, config=EnvConfig(),
                        registry=registry, out_dir=root / 'run')
                    records = [json.loads(s) for s in (root / 'run/records.jsonl').read_text().splitlines()]
                    self.assertEqual(2, len(records))
                    self.assertFalse(records[0]['solved'])
                    self.assertTrue(records[0]['gave_up'])
                    self.assertEqual(16, records[0]['cost'])
                    self.assertEqual(1, records[0]['resources']['action_budget_exhaustions'])
                    self.assertTrue(records[1]['solved'])
                    self.assertEqual(0, records[1]['cost'])
                    self.assertEqual(0, records[1]['resources']['action_budget_exhaustions'])
                    self.assertEqual({}, tracker.wait_ended(0, 5))
                    self.assertEqual(2, len(captured))
                    limit = time.monotonic() + 5
                    while True:
                        receipts = [json.loads(s) for s in (root / 'provider.jsonl').read_text().splitlines()]
                        finished = [r for r in receipts if r.get('phase') == 'finished']
                        if len(finished) == 2 or time.monotonic() >= limit:
                            break
                        time.sleep(0.01)
                    self.assertEqual(2, len(finished))
                    keys = lambda phase: Counter((r['utc_unix'], r['request_sha256'])
                        for r in receipts if r.get('phase') == phase)
                    self.assertEqual(keys('started'), keys('finished'))
                    self.assertTrue(any(not r['stream_completed'] and not r['usage_complete'] for r in finished))
                    self.assertTrue(any(r['stream_completed'] and r['usage_complete'] for r in finished))
                    audits=[json.loads(s) for s in (root/'system/reset-audit.jsonl').read_text().splitlines()]
                    fresh=[r for r in audits if r['event']=='fresh_reset']
                    bindings=[r for r in audits if r['event']=='instance_begin']
                    self.assertEqual(2,len(fresh))
                    self.assertTrue(all(r['all_writable_removed_before_start'] and r['process_restarted'] for r in fresh))
                    self.assertEqual([r['restore_id'] for r in fresh], [r['restore_id'] for r in bindings])
                finally:
                    stopping.set()
                    host.close()
                    proxy.stop()
                    bridge.close()
                    upstream.shutdown()
                    upstream.server_close()


if __name__ == '__main__':
    unittest.main()
