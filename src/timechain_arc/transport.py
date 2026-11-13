"""A plain chat-completions client; the official proxy owns real credentials."""
import json
import urllib.request


class ProxyClient:
    def __init__(self, base_url, key, config):
        if not base_url:
            raise RuntimeError("ARC_LLM_BASE_URL must point to the official tracking proxy")
        self.url = base_url.rstrip('/') + '/chat/completions'
        self.key, self.config = key, config

    def complete(self, messages):
        if not self.config.get('model'):
            raise RuntimeError('Select and freeze the exact model in config.json before evaluation')
        body = {'model': self.config['model'], 'messages': messages,
                'max_tokens': self.config['max_output_tokens'], 'stream': True,
                'stream_options': {'include_usage': True}}
        if self.config.get('effort'):
            body['reasoning'] = {'effort': self.config['effort']}
        request = urllib.request.Request(self.url, json.dumps(body).encode(),
                    {'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        # No untracked retry, fallback endpoint, SDK state, or provider-side tool.
        parts=[];done=False;finish_reason=None
        with urllib.request.urlopen(request, timeout=self.config['model_timeout_seconds']) as response:
            for line in response:
                line=line.decode('utf8').strip()
                if not line.startswith('data:'):continue
                value=line[5:].strip()
                if value=='[DONE]':done=True;break
                item=json.loads(value)
                if item.get('error'):raise RuntimeError('Tracked model stream failed: '+str(item['error'])[:1000])
                for choice in item.get('choices',[]):
                    finish_reason=choice.get('finish_reason') or finish_reason
                    content=choice.get('delta',{}).get('content')
                    if isinstance(content,str):parts.append(content)
        # These requests were still tracked. No environment action was taken,
        # and the learner can retry while preserving its paid demonstrations.
        if not done:
            raise ValueError('Incomplete tracked model stream: no completion marker. No decision was executed. Retry with a compact JSON decision using the evidence already supplied.')
        if finish_reason=='length':
            raise ValueError('Model token limit exhausted before a complete decision. Use a concise JSON decision: an executable analyze helper or a demonstration request can replace prolonged manual search. Do not repeat the same search.')
        # Reasoning deltas remain in the official accounting journal; the learner
        # consumes the final JSON decision, not a hidden deliberation transcript.
        try:
            return json.loads(''.join(parts))
        except json.JSONDecodeError as error:
            raise ValueError('Model returned no complete JSON decision. Return one compact JSON object without markdown; use executable analysis or request more evidence if uncertain.') from error
