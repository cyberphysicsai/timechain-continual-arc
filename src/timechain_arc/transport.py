"""Deadline-aware tracked chat client; the proxy owns real credentials."""
import json
import time

import httpx


class ActionBudgetExpired(TimeoutError):
    """Return a scored action before the external hosted-call deadline."""


class ProxyClient:
    def __init__(self, base_url, key, config):
        if not base_url:
            raise RuntimeError('ARC_LLM_BASE_URL must point to the official tracking proxy')
        self.url = base_url.rstrip('/') + '/chat/completions'
        self.key, self.config = key, config

    @staticmethod
    def _check_deadlines(action_deadline, request_deadline):
        now = time.monotonic()
        if action_deadline is not None and now >= action_deadline:
            raise ActionBudgetExpired('Action model-time budget exhausted; no model decision executed')
        if now >= request_deadline:
            raise ValueError('Tracked model request exceeded its wall-time budget. Use a compact executable-analysis or demonstration decision rather than repeating the same search.')

    @staticmethod
    def _stream_error(error):
        # Never echo an upstream error body, which could contain credentials.
        code = str(error.get('code', '')) if isinstance(error, dict) else ''
        message = str(error.get('message', '')) if isinstance(error, dict) else str(error)
        refused = ('subscription usage', 'subscription-only run stopped', 'not included',
                   'provider did not confirm', 'budget', 'balance', 'quota', 'payment', 'unauthorized')
        if code in ('401', '402', '403', '429') or any(s in message.lower() for s in refused):
            raise RuntimeError('Tracked provider refused further inference; no fallback or extra billing enabled')
        raise ValueError('Tracked model stream reported an error before a complete decision. No decision was executed.')

    def complete(self, messages, *, deadline=None):
        if not self.config.get('model'):
            raise RuntimeError('Select and freeze the exact model in config.json before evaluation')
        request_deadline = time.monotonic() + self.config['model_timeout_seconds']
        if deadline is not None:
            request_deadline = min(request_deadline, deadline)
        self._check_deadlines(deadline, request_deadline)
        body = {'model': self.config['model'], 'messages': messages,
                'max_tokens': self.config['max_output_tokens'], 'stream': True,
                'stream_options': {'include_usage': True}}
        if self.config.get('effort'):
            body['reasoning'] = {'effort': self.config['effort']}
        # Idle I/O is bounded separately from total model/action time. Repeated
        # reasoning chunks must not refresh the overall action deadline.
        io_timeout = min(self.config.get('stream_idle_timeout_seconds', 120),
                         max(0.001, request_deadline - time.monotonic()))
        parts = []
        done = False
        finish_reason = None
        pending = b''
        try:
            with httpx.Client(timeout=httpx.Timeout(io_timeout), trust_env=False,
                              follow_redirects=False) as client:
                self._check_deadlines(deadline, request_deadline)
                with client.stream('POST', self.url, json=body, headers={
                        'Authorization': 'Bearer ' + self.key,
                        'Content-Type': 'application/json'}) as response:
                    if response.status_code >= 400:
                        if response.status_code < 500:
                            raise RuntimeError('Tracked endpoint rejected inference with HTTP '
                                               + str(response.status_code) + '; no fallback enabled')
                        raise ValueError('Tracked endpoint returned HTTP ' + str(response.status_code)
                                         + '; no model decision was executed')
                    # Parse bytes rather than iter_lines: a stream that keeps
                    # sending bytes without a newline must still meet the clock.
                    for chunk in response.iter_bytes():
                        self._check_deadlines(deadline, request_deadline)
                        pending += chunk
                        if len(pending) > 2 * 1024**2:
                            raise ValueError('Tracked model stream line exceeded its size limit')
                        while b'\n' in pending:
                            line, pending = pending.split(b'\n', 1)
                            line = line.decode('utf8').strip()
                            if not line.startswith('data:'):
                                continue
                            value = line[5:].strip()
                            if value == '[DONE]':
                                done = True
                                break
                            item = json.loads(value)
                            if item.get('error'):
                                self._stream_error(item['error'])
                            for choice in item.get('choices', []):
                                finish_reason = choice.get('finish_reason') or finish_reason
                                content = choice.get('delta', {}).get('content')
                                if isinstance(content, str):
                                    parts.append(content)
                        if done:
                            break
        except httpx.TransportError as error:
            self._check_deadlines(deadline, request_deadline)
            raise ValueError('Tracked model transport failed (' + type(error).__name__
                             + '); no model decision was executed') from None
        self._check_deadlines(deadline, request_deadline)
        if not done:
            raise ValueError('Incomplete tracked model stream: no completion marker. No decision was executed. Retry with a compact JSON decision using the evidence already supplied.')
        if finish_reason == 'length':
            raise ValueError('Model token limit exhausted before a complete decision. Use a concise JSON decision: an executable analyze helper or a demonstration request can replace prolonged manual search. Do not repeat the same search.')
        try:
            return json.loads(''.join(parts))
        except json.JSONDecodeError as error:
            raise ValueError('Model returned no complete JSON decision. Return one compact JSON object without markdown; use executable analysis or request more evidence if uncertain.') from error
