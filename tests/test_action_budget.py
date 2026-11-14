"""Synthetic wall-time regressions; no benchmark task or paid inference."""
import json
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from continual_arc.env import GiveUp
from test_isolation import ScriptedClient, observation
from timechain_arc.learner import TimechainLearner
from timechain_arc.transport import ActionBudgetExpired, ProxyClient


@contextmanager
def slow_stream(*, newline=True, silent=False):
    captured = []
    closed = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.flush()
            try:
                for _ in range(300):
                    if not silent:
                        self.wfile.write(b': heartbeat\n\n' if newline else b'partial-line')
                        self.wfile.flush()
                    time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                closed.set()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port), captured, closed
    finally:
        server.shutdown()
        server.server_close()


def client(url, **updates):
    config = dict(model='synthetic/fixture', effort='max', max_output_tokens=128000,
                  model_timeout_seconds=5, stream_idle_timeout_seconds=0.1)
    config.update(updates)
    return ProxyClient(url, 'synthetic-proxy-key', config)


class StreamingBudgetTests(unittest.TestCase):
    def test_expired_action_does_not_issue_a_request(self):
        with patch('timechain_arc.transport.httpx.Client') as factory:
            with self.assertRaises(ActionBudgetExpired):
                client('http://127.0.0.1:1').complete([], deadline=time.monotonic() - 1)
            factory.assert_not_called()

    def test_regular_chunks_and_unterminated_lines_cannot_refresh_deadline(self):
        for newline in (True, False):
            with self.subTest(newline=newline), slow_stream(newline=newline) as (url, captured, closed):
                start = time.monotonic()
                with self.assertRaises(ActionBudgetExpired):
                    client(url).complete([], deadline=start + 0.2)
                self.assertLess(time.monotonic() - start, 1)
                self.assertEqual(1, len(captured))
                self.assertEqual(128000, captured[0]['max_tokens'])
                self.assertEqual({'effort': 'max'}, captured[0]['reasoning'])
                self.assertTrue(closed.wait(1), 'upstream should notice the closed connection')

    def test_request_deadline_is_distinct_from_the_longer_action_budget(self):
        with slow_stream() as (url, captured, _):
            start = time.monotonic()
            with self.assertRaisesRegex(ValueError, 'wall-time budget'):
                client(url, model_timeout_seconds=0.2).complete([], deadline=start + 2)
            self.assertLess(time.monotonic() - start, 1)
            self.assertEqual(1, len(captured))

    def test_inactive_socket_is_bounded_without_hidden_retry(self):
        with slow_stream(silent=True) as (url, captured, _):
            start = time.monotonic()
            with self.assertRaisesRegex(ValueError, 'ReadTimeout'):
                client(url, stream_idle_timeout_seconds=0.05).complete([], deadline=start + 2)
            self.assertLess(time.monotonic() - start, 1)
            self.assertEqual(1, len(captured))


class MechanicalChain:
    def __init__(self):
        self.receipts = []

    def route(self, query):
        return {'decision': 'MODEL', 'evidence': {'recalled': []}}

    def receipt(self, event):
        self.receipts.append(event)

    def collapse(self, *args):
        pass

    def seal(self, *args):
        pass


def bounded_learner():
    learner = TimechainLearner()
    learner.chain = MechanicalChain()
    learner._check_code = lambda: None
    learner.config['action_model_budget_seconds'] = 0.3
    return learner


class LearnerBudgetTests(unittest.TestCase):
    def test_analysis_replies_share_one_deadline_and_preserve_paid_evidence(self):
        class AnalyzeOnly(ScriptedClient):
            def __init__(self):
                super().__init__()
                self.deadlines = []

            def complete(self, messages, *, deadline=None):
                self.deadlines.append(deadline)
                time.sleep(0.1)
                reply = super().complete(messages)
                reply['program'] = 'def analyze(obs,demos): return {"pairs": len(demos)}'
                return reply

        learner = bounded_learner()
        learner.client = AnalyzeOnly()
        obs = observation()
        paid = {'input': [[7]], 'output': [[7]]}
        learner._working(obs)['demos'].append(paid)
        start = time.monotonic()
        self.assertIsInstance(learner.act(obs), GiveUp)
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertGreaterEqual(len(learner.client.contexts), 1)
        self.assertLessEqual(len(learner.client.contexts), 4)
        self.assertEqual(1, len(set(learner.client.deadlines)))
        remaining = [c['remaining_action_model_seconds'] for c in learner.client.contexts]
        self.assertEqual(sorted(remaining, reverse=True), remaining)
        self.assertTrue(all(c['paid_demonstrations'] == [paid] for c in learner.client.contexts))
        self.assertTrue(any(e['event'] == 'action_model_budget_exhausted'
                            for e in learner.chain.receipts))
        self.assertEqual(1, learner.action_budget_exhaustions)

    def test_stream_budget_exception_becomes_give_up_without_another_request(self):
        class Expired(ScriptedClient):
            def complete(self, messages, **kwargs):
                raise ActionBudgetExpired('synthetic deadline')

        learner = bounded_learner()
        learner.client = Expired()
        self.assertIsInstance(learner.act(observation()), GiveUp)
        self.assertEqual(1, learner.calls)
        self.assertEqual(1, learner.action_budget_exhaustions)

    def test_expired_budget_does_not_call_the_model(self):
        learner = bounded_learner()
        learner.config['action_model_budget_seconds'] = 0
        learner.client = ScriptedClient()
        self.assertIsInstance(learner.act(observation()), GiveUp)
        self.assertEqual(0, learner.calls)

    def test_give_up_is_decodable_for_grid_and_game_observations(self):
        learner = bounded_learner()
        for obs in (observation(), SimpleNamespace(frames=[], available_actions=[1])):
            self.assertIsInstance(learner._decode({'type': 'give_up'}, obs), GiveUp)


if __name__ == '__main__':
    unittest.main()
