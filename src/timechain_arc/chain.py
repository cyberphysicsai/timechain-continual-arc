"""Own only the learner's disposable chain; never a developer or Robodojo chain."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).parent / 'vendor' / 'skill'
DIMENSIONS = ('coherence', 'relevance', 'novelty', 'consistency', 'depth', 'covenant')


class Chain:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if not (self.root / 'chain/rings.jsonl').exists():
            (self.root / 'registry').mkdir(parents=True, exist_ok=True)
            for name in ('modalities.json', 'senses.json'):
                shutil.copyfile(SKILL / 'registry' / name, self.root / 'registry' / name)
            self.run('timechain', 'init', '--name', 'IsolatedContinualARCLearner')
            self.run('cambium', 'auto-sprout', 'on', '--registry-root', str(self.root))
        self.run('timechain', 'verify')
        toggle = json.loads((self.root / 'chain/auto_sprout.json').read_text())
        if not toggle.get('enabled'):
            raise RuntimeError('Cambium must remain enabled')

    def run(self, module, *args):
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        argv=[str(SKILL / (module + '.py')), *map(str,args), '--root', str(self.root)]
        # Large programs and paid game traces exceed OS per-argument limits.
        # Feed data over stdin, then invoke the unmodified skill entry point.
        bootstrap='import json,runpy,sys; from pathlib import Path; sys.argv=json.load(sys.stdin); sys.path.insert(0,str(Path(sys.argv[0]).parent)); runpy.run_path(sys.argv[0],run_name="__main__")'
        result = subprocess.run([sys.executable, '-I', '-c', bootstrap], input=json.dumps(argv), capture_output=True, text=True,
                                encoding='utf8', env=env, timeout=120)
        if result.returncode:
            raise RuntimeError(f'Timechain {module} failed: {result.stderr[-1600:]} {result.stdout[-1600:]}')
        return result.stdout

    def route(self, query):
        return json.loads(self.run('router', 'route', query, '--top', '5', '--json'))

    def seal(self, thought, evidence, scores, used_rings=()):
        # Only model-supplied judgments for model decisions. Mechanical receipts
        # use conservative fixed audit scores and label that provenance explicitly.
        args = ['turn', thought, '--input', evidence, '--registry-root', str(self.root)]
        for dimension in DIMENSIONS:
            value = scores[dimension]
            if type(value) is not int or not 0 <= value <= 255:
                raise ValueError('Invalid PoQ dimension')
            args += ['--' + dimension, str(value)]
        if used_rings:
            args += ['--used-rings', *map(str, used_rings)]
        output = self.run('recall', *args)
        if 'PoQ decision: SEAL' not in output:
            raise ValueError('PoQ did not approve this decision; revise it before acting')
        return output

    def collapse(self, query, context, perspectives):
        if len(perspectives) < 5:
            raise ValueError('At least five explicit single-model perspectives required')
        for p in perspectives:
            if not all(k in p for k in ('name', 'summary', 'score')):
                raise ValueError('Malformed perspective')
            if type(p['score']) is not int or not 0 <= p['score'] <= 255:
                raise ValueError('Invalid perspective score')
        path = self.root / 'current-perspectives.json'
        path.write_text(json.dumps({'query': query, 'context': context,
                                    'perspectives': perspectives}), encoding='utf8')
        return self.run('chronosynaptic', 'collapse-notes', str(path), '--seal')

    def receipt(self, event):
        return self.seal('Mechanical execution/feedback receipt, fixed audit scores: ' +
                         json.dumps(event, separators=(',', ':')),
                         'Visible benchmark action or feedback; no model-confidence claim',
                         dict(zip(DIMENSIONS, (230, 240, 100, 230, 150, 255))))
