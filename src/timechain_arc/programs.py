import json
import os
import subprocess
import sys
from pathlib import Path


def evaluate(code, kind, obs, state, demos, tried, timeout):
    request = {'code': code, 'kind': kind, 'obs': obs, 'state': state,
               'demos': demos, 'tried': tried}
    # The child's restricted language has no external I/O capabilities; the
    # official HostedLearner also isolates the complete learner with bwrap.
    env = {k: v for k, v in os.environ.items() if k in ('SYSTEMROOT', 'WINDIR', 'LANG')}
    try:
        result = subprocess.run([sys.executable, '-I', '-S', str(Path(__file__).with_name('program_worker.py'))],
            input=json.dumps(request), capture_output=True, text=True, encoding='utf8',
            timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {'error': 'Policy execution timed out'}
    if result.returncode:
        return {'error': 'Policy subprocess failed: ' + result.stderr[-1000:]}
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {'error': 'Policy returned invalid JSON'}
