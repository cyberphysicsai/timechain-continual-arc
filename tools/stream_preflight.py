"""Real subscription/proxy streaming connection check; no benchmark task."""
import argparse,json,sys
from pathlib import Path
from go_bridge import GoBridge,CANONICAL
from continual_arc_baselines.proxy import LLMProxy
from continual_arc_baselines.tracking import Tracker
from timechain_arc.transport import ProxyClient

p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path)
args=p.parse_args();args.root.mkdir(parents=True,exist_ok=False)
bridge=GoBridge(sys.stdin.readline().strip(),args.root/'provider-calls.jsonl')
tracker=Tracker();proxy=LLMProxy(bridge.start(),tracker=tracker,confined_to={CANONICAL:'max'},upstream_retries=0,credit_wait_attempts=0).start()
try:
    client=ProxyClient(proxy.base_url,'arc-proxy',{'model':CANONICAL,'effort':'max','max_output_tokens':2048,'model_timeout_seconds':120})
    reply=client.complete([{'role':'user','content':'Connection preflight only, no benchmark task: return JSON with ready set to true.'}])
    if tracker.wait_ended(0,10):raise RuntimeError('Proxy did not finish accounting for the preflight call')
    if tracker.usage.input_tokens<=0 or tracker.usage.output_tokens<=0:
        raise RuntimeError('Provider token usage was not accounted for')
    result={'purpose':'Connection preflight, not a benchmark result','reply':reply,
            'tracked_resources':tracker.usage.as_resources()}
    (args.root/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
finally:proxy.stop();bridge.close()
