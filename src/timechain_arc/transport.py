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
                'max_tokens': self.config['max_output_tokens'], 'stream': False}
        if self.config.get('effort'):
            body['reasoning'] = {'effort': self.config['effort']}
        request = urllib.request.Request(self.url, json.dumps(body).encode(),
                    {'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        # No untracked retry, fallback endpoint, SDK state, or provider-side tool.
        with urllib.request.urlopen(request, timeout=self.config['model_timeout_seconds']) as response:
            data = json.load(response)
        content = data['choices'][0]['message']['content']
        if not isinstance(content, str):
            raise ValueError('Expected a JSON-text model response')
        return json.loads(content)
