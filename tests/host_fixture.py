"""Synthetic lifecycle fixture, NOT the submitted learner or a benchmark solver."""
import os
from pathlib import Path
from timechain_arc.learner import TimechainLearner


class SyntheticClient:
    def complete(self, messages, **kwargs):
        return {'summary':'Synthetic lifecycle fixture identity mapping.',
                'perspectives':[{'name':'Fixture '+str(i),'summary':'Protocol test only.','score':245-i} for i in range(5)],
                'scores':dict(coherence=240,relevance=240,novelty=200,consistency=240,depth=220,covenant=255),
                'used_rings':[], 'task_note':'Synthetic identity fixture',
                'general_note':'Synthetic learned note', 'program':'def solve(g): return g',
                'action':None}


class FixtureLearner(TimechainLearner):
    def attach(self,*args,**kwargs):
        super().attach(*args,**kwargs)
        self.client=SyntheticClient()
    def resources(self):
        result=super().resources()
        try:
            (Path.cwd().parent/'outside-secret').read_text()
            readable=1
        except OSError:
            readable=0
        return dict(result,pid=os.getpid(),task_count=len(self.memory['tasks']),
                    controller_stage_exposed=int('CT_CONTROLLER_STAGE_ID' in os.environ),
                    outside_marker_readable=readable,
                    real_credential_exposed=int(os.environ.get('OPENROUTER_API_KEY')=='outer-synthetic-secret'))


class DeadlineFixtureLearner(TimechainLearner):
    """Short clock for the synthetic hosted slow-stream regression only."""
    def act(self, obs):
        self.config['action_model_budget_seconds'] = 2 if obs.step == 0 else 30
        return super().act(obs)
