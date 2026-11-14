"""Controller reset receipts reject missing resets and altered frozen state."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from continual_arc.env import LearnerObservation
from continual_arc_baselines.systems.hosted import HostedLearner, WRITABLE
from reset_audit import snapshot_digest
from strict_host import StrictHostedLearner


@unittest.skipUnless(sys.platform == 'linux', 'Linux filesystem metadata')
class ResetAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ct-reset-audit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.expected = self.root / 'expected'
        for name in WRITABLE:
            (self.expected / name).mkdir(parents=True)
        (self.expected / 'state/payload').write_bytes(b'synthetic state')
        (self.expected / 'home/cycle').symlink_to('cycle')
        (self.expected / 'home/dangling').symlink_to('missing')
        self.actual = self.root / 'actual'
        shutil.copytree(self.expected, self.actual, symlinks=True)

    def host(self):
        host = StrictHostedLearner(python=sys.executable, learner='unused:Fixture')
        host.workdir = self.actual
        host.frozen = self.expected
        return host

    def test_digest_covers_copy_bytes_mode_and_symlink_targets(self):
        baseline = snapshot_digest(self.expected)
        self.assertEqual(baseline, snapshot_digest(self.actual))
        self.assertEqual(1, baseline['regular_files'])
        for mutation in ('bytes', 'mode', 'link'):
            with self.subTest(mutation=mutation):
                if mutation == 'bytes':
                    (self.actual / 'state/payload').write_bytes(b'changed')
                elif mutation == 'mode':
                    os.chmod(self.actual / 'state/payload', 0o400)
                else:
                    (self.actual / 'home/dangling').unlink()
                    (self.actual / 'home/dangling').symlink_to('other-missing')
                self.assertNotEqual(baseline['sha256'], snapshot_digest(self.actual)['sha256'])

    def test_mismatch_refuses_to_start_learner(self):
        host = self.host()
        (self.actual / 'state/payload').write_bytes(b'changed')
        with patch.object(HostedLearner, '_start') as start:
            with self.assertRaisesRegex(RuntimeError, 'snapshot mismatch'):
                host._start()
            start.assert_not_called()
        event = json.loads((self.actual / 'reset-audit.jsonl').read_text())
        self.assertFalse(event['matched'])
        self.assertFalse(event['learner_process_running'])

    def test_mutated_snapshot_cannot_replace_anchored_snapshot(self):
        host = self.host()
        with patch.object(HostedLearner, '_start') as start:
            host._start()
            (self.expected / 'state/payload').write_bytes(b'altered snapshot')
            shutil.copy2(self.expected / 'state/payload', self.actual / 'state/payload')
            self.assertEqual(snapshot_digest(self.expected), snapshot_digest(self.actual))
            with self.assertRaisesRegex(RuntimeError, 'snapshot mismatch'):
                host._start()
            self.assertEqual(1, start.call_count)

    def test_every_new_instance_requires_its_own_restore(self):
        host = self.host()
        obs = lambda step: LearnerObservation(step=step, task_id='synthetic', input=[[1]],
            attempts_used=0, demo_requests_used=0, attempt_cap=8, demo_cap=8)
        with patch.object(HostedLearner, '_start'), patch.object(HostedLearner, 'act') as act:
            host._start()
            host.act(obs(0))
            host.act(obs(0))
            with self.assertRaisesRegex(RuntimeError, 'no new verified reset'):
                host.act(obs(1))
            host._start()
            host.act(obs(1))
            self.assertEqual(3, act.call_count)
        events = [json.loads(s) for s in (self.actual / 'reset-audit.jsonl').read_text().splitlines()]
        bindings = [r for r in events if r['event'] == 'instance_begin']
        self.assertEqual([1, 2], [r['restore_id'] for r in bindings])
        self.assertEqual([0, 1], [r['step'] for r in bindings])


if __name__ == '__main__':
    unittest.main()
