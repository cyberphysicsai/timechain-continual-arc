"""Prepare the organizer's verification issue from a real immutable public commit.

Writing a draft incurs no charge. Posting the issue does not pay its invoice.
Budget and billing country must come from the user, never inferred from locale.
"""
import argparse,json,re,subprocess
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--track',choices=['arc1','arc2','arc3'],required=True)
    p.add_argument('--repository',required=True)
    p.add_argument('--commit',required=True)
    p.add_argument('--contact',required=True)
    p.add_argument('--budget',required=True,type=int)
    p.add_argument('--country',required=True)
    p.add_argument('--out',required=True,type=Path)
    args=p.parse_args()
    if not re.fullmatch('[0-9a-f]{40}',args.commit):p.error('Use a full immutable commit')
    if not re.fullmatch('[A-Z]{2}',args.country):p.error('Use the user-provided two-letter billing country')
    minimum=114 if args.track=='arc3' else 5
    if not minimum<=args.budget<=1000:p.error(f'This track requires a user-authorized budget between {minimum} and 1000 USD')
    config=json.loads((Path(__file__).resolve().parents[1]/'src/timechain_arc/config.json').read_text())
    body=f'''### System name
Timechain Continual-ARC 0.1.0, Cambium on

### Track
{dict(arc1='ARC-AGI-1',arc2='ARC-AGI-2',arc3='ARC-AGI-3')[args.track]}

### Contact
{args.contact}

### Code repository
{args.repository}

### Commit
{args.commit}

### Learner
timechain_arc.learner:TimechainLearner

### Model it plays with
{config['model']}

### Teacher model
None

### Reasoning effort
{config['effort']}

### Model-call budget (USD)
{args.budget}

### Billing country
{args.country}

### Before you submit
- [x] My learner calls only the model named above, and keeps as files whatever it carries from stream to tests.
- [x] The code is public under an open licence and keeps to the README's allowed learner inputs.

### Implementation and evaluation notes
Single-model autonomous procedural learning; no subagents or task-specific starting artifacts.
Fresh isolated Timechain and Cambium registries are part of the learner's learned state.
Only public observations, charged demonstrations and feedback enter that state.
The package exposes a no-argument learner and uses ARC_LLM_BASE_URL for plain chat completions.
For this verification, use your OpenRouter tracking proxy and eligible zero-retention endpoint;
the optional OpenCode Go adapter is a separate public-development controller, not required by the learner.
All reset and frozen tests must include the entire learned Timechain and registry state in the wipe/restore.
No weight training or GPU is requested. No official verification outcome is claimed in advance.
'''
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(body,encoding='utf8')
    print('Wrote reviewable issue draft; not posted and no invoice paid: '+str(args.out))


if __name__=='__main__':main()
