"""Prepare/run all canonical controls via the official CLI, never the chat bridge.

Public Go and official OpenRouter routes are explicitly different profiles.
No fixture learner or old policy artifact enters this matrix.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from continual_arc.configs import build_schedule,test_schedule
from go_bridge import CANONICAL as GO_CANONICAL

PACKAGE=Path(__file__).resolve().parents[1]


def fingerprint():
    return {p.relative_to(PACKAGE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('src','tools') for p in sorted((PACKAGE/folder).rglob('*')) if p.is_file() and
            '__pycache__' not in p.parts and '.egg-info' not in str(p)}


def prepare(root,python,runtime):
    root.mkdir(parents=True,exist_ok=False)
    config=json.loads((PACKAGE/'src/timechain_arc/config.json').read_text())
    model,effort=config['model'],config['effort']
    import timechain_arc
    installed=Path(timechain_arc.__file__).parent
    for source in (PACKAGE/'src/timechain_arc').rglob('*'):
        if source.is_file() and source.suffix in ('.py','.json'):
            actual=installed/source.relative_to(PACKAGE/'src/timechain_arc')
            if not actual.exists() or actual.read_bytes()!=source.read_bytes():
                raise RuntimeError('Installed package differs from source; install an immutable matching build first: '+str(actual))
    stages=[]
    for track in ('arc1','arc2','arc3'):
        schedule_dir=root/'schedules'/track;schedule_dir.mkdir(parents=True)
        stream=build_schedule(track,0,epilogue=False)
        stream.save(schedule_dir/'stream.json');stream.reset_each().save(schedule_dir/'reset-stream.json')
        for test in ('seen','held-out'):
            test_schedule(track,0,test,width=1).save(schedule_dir/(test+'.json'))
            # --test accepts its full one-round schedule, then the official CLI
            # converts it to width 1 for a HostedLearner's frozen process resets.
            test_schedule(track,0,test).save(schedule_dir/(test+'-full.json'))
        hosted={'python':str(python),'runtime':[str(runtime)],
                'learner':'timechain_arc.learner:TimechainLearner','sandbox':'bwrap'}
        for role,schedule,test,resume in (
            ('stream','stream','seen',False),('stream','stream','held-out',True),
            ('reset-stream','reset-stream',None,False),('reset-seen','seen',None,False),
            ('reset-held-out','held-out',None,False),('bare-seen','seen',None,False),
            ('bare-held-out','held-out',None,False)):
            out=root/'results'/track/role
            bare=role.startswith('bare-')
            args=[sys.executable,'-m','continual_arc_baselines','run','--learner',
                  'continual_arc_baselines.systems.bare:BareModelLearner' if bare else 'strict_host:StrictHostedLearner',
                  '--learner-kwargs',json.dumps({'model':model,'max_tokens':config['max_output_tokens']} if bare else hosted),
                  '--schedule',str(schedule_dir/(schedule+'.json')),'--out',str(out),
                  '--name','Timechain '+track+' '+role,'--events','--capture-content',
                  '--confine-models',model+'='+effort,'--llm-defaults',json.dumps({'reasoning':{'effort':effort}})]
            if test:args+=['--test',str(schedule_dir/(test+'-full.json')),'--test-out',str(root/'results'/track/('learner-'+test))]
            if resume:args+=['--resume']
            stages.append({'id':track+'-'+role+('-'+test if test else ''),'argv':args,
                'out':str(out),'attempt':str(root/'attempts'/track/role),
                'expected_records':len(stream) if schedule.endswith('stream') else len(test_schedule(track,0,schedule,width=1)),
                'test_out':str(root/'results'/track/('learner-'+test)) if test else None,
                'expected_test_records':len(test_schedule(track,0,test,width=1)) if test else None})
    plan={'benchmark_commit':'7a21baaaed2f964f1deea13ea29906b949af0959',
          'model':model,'effort':effort,'seed':0,'config':config,'source_sha256':fingerprint(),
          'stages':stages,'official_verification_completed':False,
          'public_go_route_is_official_verification':False}
    (root/'plan.json').write_text(json.dumps(plan,indent=2))
    print(json.dumps({'prepared_stages':len(stages),'tracks':3,'canonical_seed':0,
        'model':model,'test_isolation':'Official HostedLearner.freeze; bwrap; strict full-reset references'},indent=2))


def execute(root,profile,cap,prices,pin,only_stage):
    import fcntl
    lease=(root/'.run.lock').open('a')
    fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    plan=json.loads((root/'plan.json').read_text())
    if fingerprint()!=plan['source_sha256']:raise RuntimeError('Source changed after matrix freeze; prepare a new run')
    model,effort=plan['model'],plan['effort']
    bridge=None
    env=dict(os.environ)
    env['PYTHONPATH']=str(PACKAGE/'tools')
    if profile=='go-public':
        if model!=GO_CANONICAL or effort!='max':raise RuntimeError('Go profile only supports its declared DeepSeek max route')
        from go_bridge import GoBridge
        key=sys.stdin.readline().strip()
        if not key:raise RuntimeError('Go credential must arrive over stdin, never a command-line argument')
        bridge=GoBridge(key,root/'provider-calls.jsonl');upstream=bridge.start()
        env['ARC_UPSTREAM_KEY']='controller-only-adapter'
        common=['--upstream',upstream,'--upstream-key-env','ARC_UPSTREAM_KEY','--provider','opencode-go-public']
    elif profile=='openai-public':
        key=sys.stdin.readline().strip()
        if not key:raise RuntimeError('OpenAI credential must arrive over stdin')
        if cap is None or cap<=0 or not prices:raise RuntimeError('Native public runs require a positive spending cap and conservative price bounds')
        env['ARC_NATIVE_OPENAI_KEY']=key
        common=['--upstream','https://api.openai.com/v1','--upstream-key-env','ARC_NATIVE_OPENAI_KEY',
                '--provider','openai-native-public','--spend-ledger',str(root/'spend-ledger.json'),
                '--spend-cap',str(cap),'--prices',Path(prices).read_text()]
    else:
        if not env.get('OPENROUTER_API_KEY'):raise RuntimeError('Configure OPENROUTER_API_KEY securely')
        if cap is None or cap<=0:raise RuntimeError('A user-authorized positive spending cap is required')
        if not prices or not pin:raise RuntimeError('Pin a ZDR-capable allowed endpoint and its conservative price bounds')
        common=['--spend-ledger',str(root/'spend-ledger.json'),'--spend-cap',str(cap),
                '--prices',Path(prices).read_text(),'--pin-providers',model+'='+pin]
    # No silent upstream model/endpoint change, no unpaid model swap on quota.
    try:
        for stage in plan['stages']:
            if fingerprint()!=plan['source_sha256']:raise RuntimeError('Source changed after matrix freeze')
            if only_stage and stage['id']!=only_stage:continue
            stamp=root/'completed'/stage['id']
            if stamp.exists():continue
            from continual_arc_baselines.attempt import Attempt
            pointer=Path(stage['out'])/'attempt.json'
            if pointer.exists():
                attempt_dir=json.loads(pointer.read_text())['attempt_dir']
            else:
                attempt_dir=str(Attempt.create(stage['id'],root=root/'accounting').directory)
            env['WORKBENCH_RUN_DIR']=attempt_dir
            args=stage['argv']+common
            if profile=='openai-public':
                args=list(args);args[2]='openai_cli'
            if (Path(stage['out'])/'records.jsonl').exists() and '--resume' not in args:
                args+=['--resume']
            print('START '+stage['id'],flush=True)
            subprocess.run(args,env=env,check=True,pass_fds=[lease.fileno()])
            if fingerprint()!=plan['source_sha256']:raise RuntimeError('Source changed during the stage; do not attest a fixed build')
            verify_complete(Path(stage['out']),stage['expected_records'])
            if stage['test_out']:verify_complete(Path(stage['test_out']),stage['expected_test_records'])
            stamp.parent.mkdir(exist_ok=True);stamp.write_text('Completed and record count verified\n')
            print('COMPLETE '+stage['id'],flush=True)
    finally:
        if bridge:bridge.close()
        lease.close()


def verify_complete(path,n):
    if not (path/'summary.json').exists():raise RuntimeError('Run has no final summary: '+str(path))
    records=[json.loads(s) for s in (path/'records.jsonl').read_text().splitlines()]
    if len(records)!=n:raise RuntimeError('Partial run: '+str(path))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run']);parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--profile',choices=['go-public','openai-public','openrouter'],default='openrouter')
    parser.add_argument('--spend-cap',type=float);parser.add_argument('--prices');parser.add_argument('--pin-provider')
    parser.add_argument('--only-stage');args=parser.parse_args();root=args.root.resolve()
    if args.action=='prepare':prepare(root,Path(sys.executable).absolute(),Path(sys.base_prefix))
    else:execute(root,args.profile,args.spend_cap,args.prices,args.pin_provider,args.only_stage)


if __name__=='__main__':main()
