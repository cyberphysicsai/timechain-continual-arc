"""Regression: the CLI wants full tests before converting hosted tests to width 1."""
import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import matrix
from continual_arc.schedule import Schedule,Slot
from continual_arc_baselines.frozen import load_test,one_at_a_time


class MatrixTests(unittest.TestCase):
    def test_prepared_frozen_inputs_pass_the_actual_official_cli_guard(self):
        def stream(track,seed,epilogue=False):
            return Schedule(name='synthetic stream',seed=seed,slots=[Slot('fixture',1,'stream'),Slot('fixture',2,'stream')],sources={'fixture':'synthetic'})
        def test(track,seed,kind,width=None):
            users=2 if width is None else width
            return Schedule(name='synthetic '+kind,seed=seed,
                slots=[Slot('fixture',i,kind+'-test',reset_before=users==1,user=i%users) for i in range(2)],
                sources={'fixture':'synthetic'},users=users,meta={'test':kind,'width':users})
        with tempfile.TemporaryDirectory() as temp,patch.object(matrix,'build_schedule',stream),patch.object(matrix,'test_schedule',test):
            root=Path(temp)/'matrix'
            matrix.prepare(root,Path(sys.executable),Path(sys.base_prefix))
            plan=json.loads((root/'plan.json').read_text())
            for stage in plan['stages']:
                if stage['test_out']:
                    path=stage['argv'][stage['argv'].index('--test')+1]
                    accepted=load_test(path)
                    self.assertEqual({0},set(accepted.rounds()))
                    converted=one_at_a_time(accepted)
                    self.assertEqual(1,converted.users)
                    self.assertTrue(all(slot.reset_before for slot in converted.slots))


if __name__=='__main__':unittest.main()
