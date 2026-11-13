"""Native OpenAI public-run compatibility; not organizer private verification.

Uses the official proxy and tracker without fabricating provider-reported USD
costs. Its spend ledger conservatively charges the whole reservation when the
provider supplies no USD cost. No credentials enter this module or learner state.
"""
import json
from pathlib import Path

from continual_arc_baselines.proxy import LLMProxy


def native_options(config):
    if config['model'] != 'openai/gpt-6.1-sol' or config['effort'] != 'xhigh':
        raise ValueError('Native compatibility is pinned to GPT-6.1 Sol xhigh')
    return dict(body_overrides={'model': 'gpt-6.1-sol', 'reasoning_effort': 'xhigh',
        'max_completion_tokens': config['max_output_tokens'], 'store': False,
        'service_tier': 'default'},
        body_drop=('reasoning', 'max_tokens', 'provider', 'usage', 'session_id'))


class NativeOpenAIProxy(LLMProxy):
    def __init__(self, *args, **kwargs):
        config=json.loads((Path(__file__).resolve().parents[1]/'src/timechain_arc/config.json').read_text())
        options=native_options(config)
        kwargs['body_overrides']={**kwargs.get('body_overrides',{}), **options['body_overrides']}
        kwargs['body_drop']=tuple(set(kwargs.get('body_drop',())) | set(options['body_drop']))
        # Quota/auth failures must surface without repeated paid requests.
        kwargs['upstream_retries']=0
        kwargs['credit_wait_attempts']=0
        super().__init__(*args,**kwargs)


def main():
    from continual_arc_baselines import cli
    cli.LLMProxy=NativeOpenAIProxy
    return cli.main()


if __name__=='__main__':
    raise SystemExit(main())
