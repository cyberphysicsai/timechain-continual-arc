"""Exercise the actual official bwrap host, process restarts and frozen restores."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from strict_host import StrictHostedLearner
from continual_arc.env import LearnerObservation, SubmitFeedback


@unittest.skipUnless(sys.platform=='linux','Official bwrap integration runs on Linux')
class HostedIsolationTests(unittest.TestCase):
    def test_real_host_freezes_files_and_process_and_hides_outside_state(self):
        package=Path(__file__).resolve().parents[1]
        runtime=Path(sys.base_prefix)
        previous=os.environ.get('OPENROUTER_API_KEY')
        os.environ['OPENROUTER_API_KEY']='outer-synthetic-secret'
        with tempfile.TemporaryDirectory(prefix='ct-official-host-') as temp:
            work=Path(temp)/'system';work.mkdir()
            (work/'outside-secret').write_text('not learner state')
            host=StrictHostedLearner(python=sys.executable,learner='host_fixture:FixtureLearner',
                code=str(package/'tests'),runtime=[str(runtime)],sandbox='bwrap',timeout_s=120)
            try:
                host.attach(work,{})
                def learn(step,task):
                    host.begin_round(1)
                    obs=LearnerObservation(step=step,task_id=task,input=[[1]],attempts_used=0,
                        demo_requests_used=0,attempt_cap=8,demo_cap=8)
                    a=host.act(obs)
                    host.observe(obs,a,SubmitFeedback(correct=True,valid=True,attempts_used=0,failed=False))
                    host.end_round()
                learn(0,'synthetic-stream')
                before=host.resources()
                before_host_pid=host._proc.pid
                self.assertEqual(1,before['task_count'])
                self.assertEqual(0,before['outside_marker_readable'])
                self.assertEqual(0,before['real_credential_exposed'])
                host.reset()
                self.assertNotEqual(before_host_pid,host._proc.pid)
                self.assertEqual(0,host.resources()['task_count'])
                learn(0,'synthetic-stream')
                before_host_pid=host._proc.pid
                host.freeze()
                original=(host.frozen/'state/learner/committed/timechain/chain/rings.jsonl').read_bytes()
                first=host.resources()
                first_host_pid=host._proc.pid
                self.assertNotEqual(before_host_pid,first_host_pid)
                learn(1,'synthetic-test-only')
                self.assertEqual(2,host.resources()['task_count'])
                host.reset()
                restored=host.resources()
                self.assertNotEqual(first_host_pid,host._proc.pid)
                self.assertEqual(1,restored['task_count'])
                self.assertEqual(0,restored['model_decisions'])
                self.assertEqual(original,(host.state_dir/'learner/committed/timechain/chain/rings.jsonl').read_bytes())
                self.assertEqual(original,(host.frozen/'state/learner/committed/timechain/chain/rings.jsonl').read_bytes())
                self.assertNotIn('synthetic-test-only',(host.state_dir/'learner/committed/memory.json').read_text())
            finally:
                host.close()
                if previous is None:os.environ.pop('OPENROUTER_API_KEY',None)
                else:os.environ['OPENROUTER_API_KEY']=previous


if __name__=='__main__':unittest.main()
