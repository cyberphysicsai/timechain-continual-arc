"""Separate public smoke pilot. Not a canonical result or official verification."""
import argparse,json,os,subprocess,sys
from pathlib import Path
from go_bridge import GoBridge,CANONICAL
from continual_arc_baselines.attempt import Attempt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--stop-after',type=int,default=3)
    args=parser.parse_args();root=args.root.resolve();root.mkdir(parents=True,exist_ok=False)
    bridge=GoBridge(sys.stdin.readline().strip(),root/'provider-calls.jsonl')
    upstream=bridge.start()
    attempt=Attempt.create('timechain-public-pilot',root=root/'accounting')
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parent),WORKBENCH_RUN_DIR=str(attempt.directory),
             ARC_UPSTREAM_KEY='controller-only-adapter')
    kwargs={'python':sys.executable,'runtime':[sys.base_prefix],
            'learner':'timechain_arc.learner:TimechainLearner','sandbox':'bwrap'}
    argv=[sys.executable,'-m','continual_arc_baselines','run','--learner','strict_host:StrictHostedLearner',
          '--learner-kwargs',json.dumps(kwargs),'--config','arc1-smoke','--seed','710112',
          '--no-epilogue','--out',str(root/'run'),'--name','Timechain independent public smoke pilot',
          '--stop-after',str(args.stop_after),'--events','--capture-content','--upstream',upstream,
          '--upstream-key-env','ARC_UPSTREAM_KEY','--confine-models',CANONICAL+'=max',
          '--provider','opencode-go-public','--llm-defaults',json.dumps({'reasoning':{'effort':'max'}})]
    (root/'purpose.json').write_text(json.dumps({'kind':'public development pilot','official_submission':False,
        'model':CANONICAL,'effort':'max','argv':argv},indent=2))
    try:
        subprocess.run(argv,env=env,check=True)
        rows=(root/'run/records.jsonl').read_text().splitlines()
        if len(rows)!=args.stop_after:raise RuntimeError('Pilot did not execute the requested instance count')
    finally:bridge.close()


if __name__=='__main__':main()
