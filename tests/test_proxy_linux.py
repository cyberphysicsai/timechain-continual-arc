"""No paid model: verify the real official proxy tracks and salts fixture calls."""
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from continual_arc_baselines.proxy import LLMProxy
from continual_arc_baselines.tracking import Tracker
from timechain_arc.transport import ProxyClient


@unittest.skipUnless(sys.platform=='linux','Official proxy uses fcntl on Linux')
class ProxyTests(unittest.TestCase):
    def test_real_proxy_tracks_all_calls_and_changes_reset_salt(self):
        captured=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                captured.append(request)
                data={'id':'synthetic-fixture','model':'synthetic/fixture','choices':[{'message':{'content':'{"fixture":true}'},'finish_reason':'stop'}],
                      'usage':{'prompt_tokens':10,'completion_tokens':3,'total_tokens':13,'cost':0}}
                if request.get('stream'):
                    chunks=[{'id':'synthetic-fixture','model':'synthetic/fixture','choices':[{'index':0,'delta':{'content':'{"fixture":true}'},'finish_reason':'stop'}]},
                            {'id':'synthetic-fixture','model':'synthetic/fixture','choices':[],'usage':data['usage']}]
                    encoded=(''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode()
                    content_type='text/event-stream'
                else:encoded=json.dumps(data).encode();content_type='application/json'
                self.send_response(200)
                self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
            def log_message(self,*args):pass
        upstream=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
        tracker=Tracker()
        proxy=LLMProxy('http://127.0.0.1:'+str(upstream.server_port),tracker=tracker,
                       confined_to={'synthetic/fixture':'medium'},upstream_retries=0).start()
        try:
            config={'model':'synthetic/fixture','effort':'medium','max_output_tokens':100,'model_timeout_seconds':30}
            client=ProxyClient(proxy.base_url,'arc-proxy',config)
            self.assertEqual({'fixture':True},client.complete([{'role':'user','content':'fixture request'}]))
            self.assertEqual({},tracker.wait_ended(0,5))
            tracker.epoch += 1  # TrackedLearner.reset advances this official salt epoch.
            self.assertEqual({'fixture':True},client.complete([{'role':'user','content':'fixture request'}]))
            self.assertEqual({},tracker.wait_ended(0,5))
            self.assertEqual(2,len(captured))
            self.assertNotEqual(captured[0]['messages'][0]['content'],captured[1]['messages'][0]['content'])
            self.assertEqual(2,tracker.usage.requests)
            self.assertEqual(0,tracker.usage.failed_requests)
            self.assertEqual(20,tracker.usage.input_tokens)
            self.assertEqual(6,tracker.usage.output_tokens)
        finally:
            proxy.stop();upstream.shutdown();upstream.server_close()


if __name__=='__main__':unittest.main()
