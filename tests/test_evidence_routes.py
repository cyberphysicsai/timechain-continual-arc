"""Evidence the router supplies must be citable on every route variant."""
import json
import tempfile
import unittest
from pathlib import Path

from test_isolation import ScriptedClient, observation
from timechain_arc.learner import TimechainLearner
from continual_arc.env import Submit


class CitedClient(ScriptedClient):
    def __init__(self, rings):
        super().__init__()
        self.rings = rings

    def complete(self, messages):
        reply = super().complete(messages)
        reply['used_rings'] = self.rings
        return reply


class EvidenceRouteTests(unittest.TestCase):
    def check_route(self, route, rings, accepted):
        with tempfile.TemporaryDirectory() as directory:
            learner = TimechainLearner()
            learner.attach(Path(directory), {})
            learner.begin_round(1)
            if callable(route):
                route, rings = route(learner)
            learner.client = CitedClient(rings)
            obs = observation()
            working = learner._working(obs)
            try:
                if accepted:
                    self.assertIsInstance(learner._model(working, obs, route), Submit)
                    self.assertEqual(1, len(learner.client.contexts))
                else:
                    with self.assertRaisesRegex(ValueError, 'not supplied'):
                        learner._model(working, obs, route)
                    self.assertEqual({}, learner.memory['tasks'])
            finally:
                learner.close()

    def test_replay_antecedent_is_supplied_evidence_including_ring_zero(self):
        self.check_route({'decision': 'REPLAY', 'evidence': {'antecedent': {'ring': 0}}}, [0], True)

    def test_recalled_evidence_remains_citable(self):
        self.check_route({'decision': 'MODEL', 'evidence': {'recalled': [{'ring': 0}]}}, [0], True)

    def test_both_evidence_shapes_are_accepted_together(self):
        def supplied(learner):
            learner.chain.receipt({'event': 'synthetic_evidence', 'purpose': 'mixed-route citation test'})
            last = json.loads((learner.round_dir / 'timechain/chain/rings.jsonl').read_text().splitlines()[-1])['index']
            return {'decision': 'PARTIAL', 'evidence': {
                'recalled': [{'ring': 0}], 'antecedent': {'ring': last}}}, [0, last]
        self.check_route(supplied, None, True)

    def test_unknown_reference_remains_rejected(self):
        self.check_route({'decision': 'REPLAY', 'evidence': {'antecedent': {'ring': 0}}}, [999999], False)

    def test_noninteger_references_remain_rejected(self):
        for rings in ([False], ['0'], [0.0]):
            with self.subTest(rings=rings):
                self.check_route({'decision': 'REPLAY', 'evidence': {'antecedent': {'ring': 0}}}, rings, False)

    def test_empty_reference_list_is_allowed(self):
        self.check_route({'decision': 'MODEL', 'evidence': {'recalled': []}}, [], True)


if __name__ == '__main__':
    unittest.main()
