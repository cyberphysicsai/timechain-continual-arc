"""Write official submission manifests only for complete canonical controls.

This never invents an official verification status or a leaderboard score.
It runs the upstream checker with absolute local paths, then a portable copy
can be committed under the benchmark repository's results/ tree for its PR.
"""
import argparse,json,subprocess,sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--benchmark-checkout',required=True,type=Path)
    parser.add_argument('--code-url',required=True)
    parser.add_argument('--contact',required=True)
    args=parser.parse_args();root=args.root.resolve()
    plan=json.loads((root/'plan.json').read_text())
    missing=[s['id'] for s in plan['stages'] if not (root/'completed'/s['id']).exists()]
    if missing:raise SystemExit('Incomplete matrix stages; no submission manifest written: '+', '.join(missing))
    for track in ('arc1','arc2','arc3'):
        base=root/'results'/track
        manifest={'name':'Timechain Continual-ARC 0.1.1','learner':'Task-naive Timechain program learner, Cambium on',
                  'student':plan['model'],'effort':plan['effort'],'teacher':None,
                  'code':args.code_url,'contact':args.contact,'reset':str(base/'reset-stream'),
                  'tests':{test:{'run':str(base/('learner-'+test)),
                    'reset':str(base/('reset-'+test)),'bare':str(base/('bare-'+test))} for test in ('seen','held-out')},
                  'notes':'Canonical local controlled run. The model/provider route is documented in the run journals. Organizer verification is separate and not claimed.'}
        (base/'stream/submission.json').write_text(json.dumps(manifest,indent=2))
        command=[sys.executable,str(args.benchmark_checkout/'scripts/check_result.py'),str(base/'stream')]
        result=subprocess.run(command,capture_output=True,text=True)
        (base/'check-result.txt').write_text(result.stdout+result.stderr)
        print(result.stdout,end='')
        if result.returncode:raise SystemExit(result.returncode)


if __name__=='__main__':main()
