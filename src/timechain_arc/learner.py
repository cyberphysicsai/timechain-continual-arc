"""Task-naive autonomous learner; stream memory commits at end_round only."""
import copy
import dataclasses
import hashlib
import json
import os
import shutil
from pathlib import Path

from continual_arc.learner import BaseLearner
from continual_arc.env import Submit, RequestDemos, GiveUp
from continual_arc.arc3.env import ResetLevel
from continual_arc.arc3.instances import Act

from .chain import Chain, DIMENSIONS
from .programs import evaluate
from .prompt import SYSTEM
from .transport import ProxyClient


def as_json(value):
    if hasattr(value, 'to_json'):
        return value.to_json()
    if dataclasses.is_dataclass(value):
        return {f.name: as_json(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (list, tuple)):
        return [as_json(v) for v in value]
    if isinstance(value, dict):
        return {k: as_json(v) for k, v in value.items()}
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def event_preview(event):
    event=copy.deepcopy(event)
    feedback=event.get('feedback',{})
    for field in ('pairs','demonstration'):
        if field in feedback:
            value=feedback.pop(field)
            feedback[field+'_sha256']=digest(value)
    return event


class TimechainLearner(BaseLearner):
    concurrent_users = False

    def __init__(self):
        self.config = json.loads(Path(__file__).with_name('config.json').read_text())
        self.root = None
        self.chain = None
        self.client = None
        self.memory = {'tasks': {}, 'general_note': ''}
        self.working = {}
        self.calls = self.policy_calls = self.policy_errors = 0
        self.code_sha256 = None

    def _code_hashes(self):
        base=Path(__file__).parent
        return {str(path.relative_to(base)):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in base.rglob('*') if path.is_file() and path.suffix in ('.py','.json')}

    def _check_code(self):
        if self._code_hashes()!=self.code_sha256:
            raise RuntimeError('Installed learner code changed during evaluation; stop this partial run')

    def attach(self, workdir, environ, tracker=None):
        # HostedLearner makes this the only persistent writable state. No
        # developer chain, previous benchmark artifacts, task files or schedules.
        self.root = Path(workdir).resolve() / 'learner'
        self.root.mkdir(parents=True, exist_ok=True)
        self.code_sha256 = self._code_hashes()
        self.state_dir = self.root / 'committed'
        previous = self.root / 'previous'
        if not self.state_dir.exists() and previous.exists():
            previous.rename(self.state_dir)
        if self.state_dir.exists() and previous.exists():
            shutil.rmtree(previous)
        self.state_dir.mkdir(exist_ok=True)
        path = self.state_dir / 'memory.json'
        if path.exists():
            self.memory = json.loads(path.read_text())
        self.chain = Chain(self.state_dir / 'timechain')
        base = environ.get('ARC_LLM_BASE_URL') or os.environ.get('ARC_LLM_BASE_URL')
        if base:
            self.client = ProxyClient(base, environ.get('ARC_LLM_API_KEY', 'arc-proxy'), self.config)

    def reset(self):
        # The reference must forget EVERYTHING learned, including rings and
        # faculties. The controller retains run evidence outside this sandbox.
        # This explicit benchmark reset discards a disposable copy, never edits
        # a retained stream snapshot or an unrelated chain.
        if self.root is None:
            raise RuntimeError('Attach learner before reset')
        if self.root.name != 'learner':
            raise RuntimeError('Unexpected owned state root')
        shutil.rmtree(self.root)
        self.root.mkdir()
        self.state_dir = self.root / 'committed'
        self.state_dir.mkdir()
        self.memory = {'tasks': {}, 'general_note': ''}
        self.working = {}
        self.chain = Chain(self.state_dir / 'timechain')

    def begin_round(self, n_instances):
        if n_instances != 1:
            raise RuntimeError('Use the official width-1 protocol for this single-core learner')
        self.working = {}
        self.round_dir = self.root / 'working'
        if self.round_dir.exists():
            shutil.rmtree(self.round_dir)
        shutil.copytree(self.state_dir, self.round_dir)
        self.chain = Chain(self.round_dir / 'timechain')

    def _working(self, obs):
        key = (obs.user, obs.step, obs.task_id)
        if key not in self.working:
            task = copy.deepcopy(self.memory['tasks'].get(obs.task_id, {}))
            self.working[key] = {'task': task, 'demos': task.get('demos', []),
                                 'program': task.get('program'), 'state': {},
                                 'tried': [], 'history': [], 'calls': 0,
                                 'analysis': None,
                                 'recalled_tasks': {},
                                 'program_blocked': task.get('program_rejected',False), 'pending_general': None}
        return self.working[key]

    def _decode(self, action, obs):
        if not isinstance(action, dict):
            raise ValueError('Action must be an object')
        kind = action.get('type')
        game = hasattr(obs, 'frames')
        if kind == 'request_demo':
            if obs.demo_requests_used >= obs.demo_cap:
                raise ValueError('No demonstrations remain')
            return RequestDemos()
        if kind == 'give_up' and not game:
            return GiveUp()
        if kind == 'reset' and game:
            return ResetLevel()
        if kind == 'act' and game:
            number = action['id']
            if type(number) is not int or number not in obs.available_actions:
                raise ValueError('Action not offered')
            x, y = action.get('x'), action.get('y')
            if number == 6 and (type(x) is not int or type(y) is not int or not 0 <= x < 64 or not 0 <= y < 64):
                raise ValueError('Invalid click')
            return Act(number, x if number == 6 else None, y if number == 6 else None)
        if kind == 'submit' and not game:
            from .program_worker import grid
            return Submit(grid(action['grid']))
        raise ValueError('Action type does not fit this observation')

    def _program(self, w, obs):
        self.policy_calls += 1
        result = evaluate(w['program'], 'game' if hasattr(obs, 'frames') else 'grid',
                          as_json(obs), w['state'], w['demos'], w['tried'],
                          self.config['policy_timeout_seconds'])
        if 'error' in result:
            self.policy_errors += 1
            w['program_blocked'] = True
            w['history'].append({'policy_error': result['error']})
            self.chain.receipt({'event': 'policy_error', 'task': obs.task_id,
                                'program_sha256': digest(w['program']), 'error': result['error']})
            return None
        w['state'] = result['state']
        w['analysis'] = result.get('analysis')
        if result['action'] is None:
            return None
        try:
            return self._decode(result['action'], obs)
        except ValueError as error:
            w['program_blocked'] = True
            w['history'].append({'policy_error': str(error)})
            self.policy_errors += 1
            return None

    def _model(self, w, obs, route):
        if self.client is None:
            raise RuntimeError('A tracked model endpoint is required; no chat/manual fallback is allowed')
        context = {'observation': as_json(obs),
                   'task_memory': {k:v for k,v in w['task'].items() if k not in ('program','demos')},
                   'current_program': w['program'], 'paid_demonstrations': w['demos'],
                   'instance_program_state': w['state'], 'tried_grids': w['tried'],
                   'computed_analysis': w['analysis'],
                   'program_blocked': w['program_blocked'],
                   'learned_task_index': [{'task_id': key, 'note': value.get('note',''),
                       'program_sha256': digest(value.get('program')), 'demonstrations': len(value.get('demos',[]))}
                       for key,value in self.memory['tasks'].items()],
                   'recalled_task_programs': w['recalled_tasks'],
                   'recent_events': [event_preview(e) for e in w['history'][-self.config['max_history_events']:]],
                   'general_note': self.memory['general_note'], 'timechain_route': route}
        self.calls += 1
        w['calls'] += 1
        reply = self.client.complete([{'role': 'system', 'content': SYSTEM},
                                     {'role': 'user', 'content': json.dumps(context)}])
        self._check_code()
        if not isinstance(reply, dict):
            raise ValueError('Expected an object')
        allowed = {r['ring'] for r in route.get('evidence', {}).get('recalled', [])}
        used = reply.get('used_rings', [])
        if any(type(r) is not int or r not in allowed for r in used):
            raise ValueError('Claimed ring was not supplied as evidence')
        self.chain.collapse('Choose next action from permitted observation for ' + obs.task_id,
                            'Explicit model judgments; observation SHA256 ' + digest(as_json(obs)),
                            reply['perspectives'])
        self.chain.seal(json.dumps(reply, separators=(',', ':')),
                        'Task ' + obs.task_id + '; observation SHA256 ' + digest(as_json(obs)),
                        reply['scores'], used)
        if isinstance(reply.get('task_note'), str):
            w['task']['note'] = reply['task_note']
        if isinstance(reply.get('general_note'), str):
            w['pending_general'] = reply['general_note']
        recalled = reply.get('recall_tasks', [])
        if not isinstance(recalled,list) or len(recalled)>5 or any(key not in self.memory['tasks'] for key in recalled):
            raise ValueError('Recall only up to five previously learned task IDs from the supplied index')
        for key in recalled:
            w['recalled_tasks'][key] = copy.deepcopy(self.memory['tasks'][key])
        if reply.get('program') is not None:
            if not isinstance(reply['program'], str):
                raise ValueError('Program must be source text')
            w['program'] = reply['program']
            w['program_blocked'] = False
            w['state'] = {}
        if reply.get('action') is not None:
            return self._decode(reply['action'], obs)
        if w['program'] and not w['program_blocked']:
            return self._program(w, obs)
        if not recalled:
            raise ValueError('No executable action: the current program is absent or blocked. Supply a revised program or an explicit action; null keeps the blocked program.')
        return None

    def act(self, obs):
        if self.chain is None:
            raise RuntimeError('Learner was not attached')
        self._check_code()
        w = self._working(obs)
        route = self.chain.route('Determine a grounded action for task ' + obs.task_id)
        action = self._program(w, obs) if w['program'] and not w['program_blocked'] else None
        while action is None and w['calls'] < self.config['max_model_calls_per_instance']:
            try:
                action = self._model(w, obs, route)
            except (ValueError, KeyError, TypeError) as error:
                w['history'].append({'response_error': str(error)[:1000]})
                self.chain.receipt({'event': 'model_response_rejected', 'error': str(error)[:1000]})
        if action is None:
            # A runaway guard, never a free action. Runner charges full failure.
            action = GiveUp()
        self.chain.receipt({'event': 'action', 'task': obs.task_id,
                            'observation_sha256': digest(as_json(obs)), 'action': as_json(action)})
        return action

    def observe(self, obs, action, feedback):
        w = self._working(obs)
        event = {'action': type(action).__name__, 'feedback': as_json(feedback)}
        if isinstance(action, Submit):
            w['tried'].append(as_json(action.grid))
            if not getattr(feedback, 'correct', False):
                w['program_blocked'] = True
        if hasattr(feedback, 'pairs'):
            for pair in as_json(feedback.pairs):
                if pair not in w['demos']:
                    w['demos'].append(pair)
        if hasattr(feedback, 'demonstration') and feedback.demonstration is not None:
            demo = as_json(feedback.demonstration)
            if demo not in w['demos']:
                w['demos'].append(demo)
        w['history'].append(event)
        self.chain.receipt({'event': 'feedback', 'task': obs.task_id,
                            'action_type': type(action).__name__, 'feedback': as_json(feedback)})

    def end_round(self):
        for (_, _, task_id), w in self.working.items():
            w['task'].update(program=w['program'], demos=w['demos'],program_rejected=w['program_blocked'])
            w['task']['recent_outcomes'] = [event_preview(e) for e in w['history'][-8:]]
            self.memory['tasks'][task_id] = w['task']
            if w['pending_general'] is not None:
                self.memory['general_note'] = w['pending_general']
        temp = self.round_dir / 'memory.pending'
        temp.write_text(json.dumps(self.memory), encoding='utf8')
        temp.replace(self.round_dir / 'memory.json')
        self.chain.run('timechain', 'verify')
        before=(self.state_dir/'timechain/chain/rings.jsonl').read_bytes()
        after=(self.round_dir/'timechain/chain/rings.jsonl').read_bytes()
        if not after.startswith(before):
            raise RuntimeError('Committed chain prefix changed')
        previous=self.root/'previous'
        self.state_dir.rename(previous)
        self.round_dir.rename(self.state_dir)
        shutil.rmtree(previous)
        self.chain=Chain(self.state_dir/'timechain')
        self.working = {}

    def resources(self):
        return {'model_decisions': self.calls, 'policy_executions': self.policy_calls,
                'policy_errors': self.policy_errors,
                'task_memory_bytes': len(json.dumps(self.memory))}

    def describe(self):
        return {'method': 'Single-model task-naive Timechain program learner',
                'model': self.config['model'], 'reasoning_effort': self.config['effort'],
                'cambium': True, 'subagents': False, 'task_artifacts_preloaded': False}

    def close(self):
        if self.chain:
            self.chain.run('timechain', 'verify')
