"""Regression checks for observed truncated reasoning and interrupted streams."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

from test_isolation import ScriptedClient,observation
from timechain_arc.learner import TimechainLearner
from timechain_arc.transport import ProxyClient
from continual_arc.env import Submit


class RecoveryTests(unittest.TestCase):
    def test_stream_failures_provide_actionable_feedback_without_hidden_retries(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                n=len(received)
                chunk={'choices':[{'delta':{'content':'' if n<3 else '{"ready":true}'},
                    'finish_reason':'length' if n==1 else 'stop'}]}
                body=('data: '+json.dumps(chunk)+'\n\n'+('data: [DONE]\n\n' if n!=2 else '')).encode()
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        client=ProxyClient('http://127.0.0.1:'+str(server.server_port),'fixture',
            {'model':'synthetic/fixture','max_output_tokens':100,'model_timeout_seconds':5})
        try:
            with self.assertRaisesRegex(ValueError,'token limit'):
                client.complete([{'role':'user','content':'fixture'}])
            self.assertEqual(1,len(received))
            with self.assertRaisesRegex(ValueError,'Incomplete tracked model stream'):
                client.complete([{'role':'user','content':'fixture'}])
            self.assertEqual(2,len(received))
            self.assertEqual({'ready':True},client.complete([{'role':'user','content':'fixture'}]))
            self.assertEqual(3,len(received))
        finally:
            server.shutdown();server.server_close()

    def test_learner_retry_retains_paid_evidence_and_records_rejection(self):
        class InterruptedOnce(ScriptedClient):
            def __init__(self):super().__init__();self.failed=False
            def complete(self,messages):
                if not self.failed:
                    self.failed=True
                    raise ValueError('Incomplete tracked model stream: fixture')
                return super().complete(messages)
        with tempfile.TemporaryDirectory() as directory:
            learner=TimechainLearner();learner.attach(Path(directory),{});learner.begin_round(1)
            client=InterruptedOnce();learner.client=client
            obs=observation();w=learner._working(obs)
            pair={'input':[[7]],'output':[[7]]};w['demos'].append(pair)
            action=learner.act(obs)
            self.assertIsInstance(action,Submit)
            self.assertEqual(2,w['calls'])
            context=client.contexts[-1]
            self.assertEqual([pair],context['paid_demonstrations'])
            self.assertTrue(any('response_error' in event for event in context['recent_events']))
            learner.end_round();learner.close()


if __name__=='__main__':unittest.main()
