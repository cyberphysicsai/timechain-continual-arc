"""Public-run-only OpenCode Go transport adapter. Not an official verification route.

The official accounting proxy sits in front of this controller-side adapter.
It maps a declared canonical model to one subscription alias, carries the cache
salt into the required session header, and never falls back to a different model.
The credential is supplied in memory by the launch process, never to the learner.
"""
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

CANONICAL='deepseek/deepseek-v4.1-flash-1017'
ALIAS='deepseek-v4.1-flash'
URL='https://opencode.ai/inference/go/openai/v1/chat/completions'


class GoBridge:
    def __init__(self,key,journal):
        self.key=key;self.journal=journal;self.lock=threading.Lock();self.stopped=False
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path!='/chat/completions':return self.reply(404,{'error':'Unknown endpoint'})
                if owner.stopped:return self.reply(403,{'error':'Subscription-only run stopped'})
                size=int(self.headers.get('Content-Length',0))
                if size>32*1024**2:return self.reply(413,{'error':'Request too large'})
                incoming=json.loads(self.rfile.read(size))
                if incoming.get('model')!=CANONICAL:return self.reply(400,{'error':'Undeclared model'})
                streaming=bool(incoming.get('stream'))
                # Official proxy inserts a reset-specific salt as the first line.
                first=incoming['messages'][0]['content']
                if not isinstance(first,str) or not first.startswith('['):
                    return self.reply(400,{'error':'Missing official proxy cache-isolation salt'})
                session=hashlib.sha256(first.split('\n',1)[0].encode()).hexdigest()
                body={'model':ALIAS,'messages':incoming['messages'],'reasoning_effort':'max',
                      'max_tokens':incoming.get('max_tokens',65536),'stream':streaming}
                if streaming:body['stream_options']={'include_usage':True}
                if 'response_format' in incoming:body['response_format']=incoming['response_format']
                encoded=json.dumps(body).encode()
                request=urllib.request.Request(URL,encoded,{'Authorization':'Bearer '+owner.key,
                    'Content-Type':'application/json','User-Agent':'timechain-continual-arc/0.1.0',
                    'x-opencode-session':session})
                receipt={'utc_unix':time.time(),'requested_model':CANONICAL,'provider_alias':ALIAS,
                         'effort':'max','request_sha256':hashlib.sha256(encoded).hexdigest()}
                owner.record(dict(receipt,phase='started',status='started'))
                headers_sent=False
                try:
                    with urllib.request.urlopen(request,timeout=1800) as response:
                        if streaming:
                            self.send_response(200);self.send_header('Content-Type','text/event-stream')
                            self.send_header('Connection','close');self.end_headers();self.wfile.flush()
                            headers_sent=True
                            usage={};response_model=None;done=False
                            for rawline in response:
                                line=rawline.decode('utf8').strip()
                                if line.startswith('data:') and line[5:].strip()!='[DONE]':
                                    item=json.loads(line[5:].strip())
                                    if item.get('usage'):usage=item['usage']
                                    response_model=item.get('model') or response_model
                                if line.startswith('data:') and line[5:].strip()=='[DONE]':
                                    done=True
                                    source=usage.get('opencode',{}).get('billing_source','')
                                    if float(usage.get('cost',0) or 0)>0 or not source.startswith('go'):
                                        owner.stopped=True
                                        self.wfile.write(b'data: {"error":"Provider did not confirm included subscription usage"}\n\n')
                                self.wfile.write(rawline);self.wfile.flush()
                            owner.record(dict(receipt,phase='finished',status=200,response_model=response_model,
                                              usage=usage,stream_completed=done))
                            return
                        data=json.load(response)
                    receipt.update(phase='finished',status=200,response_model=data.get('model'),usage=data.get('usage'))
                    usage=data.get('usage',{});source=usage.get('opencode',{}).get('billing_source','')
                    if float(usage.get('cost',0) or 0)>0 or not source.startswith('go'):
                        owner.stopped=True;receipt['stopped']='Response was not included subscription usage'
                        owner.record(receipt)
                        return self.reply(403,{'error':'Stopped: provider did not report included Go usage'})
                    owner.record(receipt);return self.reply(200,data)
                except urllib.error.HTTPError as error:
                    receipt.update(status=error.code,error='Provider rejected request')
                    if error.code in (401,402,403,429):owner.stopped=True
                    owner.record(receipt)
                    return self.reply(403 if owner.stopped else error.code,{'error':{'message':'Subscription provider rejected request; see controller receipt','code':str(error.code)}})
                except Exception as error:
                    receipt.update(status=502,error=type(error).__name__);owner.record(receipt)
                    if headers_sent:
                        self.wfile.write(b'data: {"error":"Subscription stream interrupted"}\n\n')
                        self.wfile.flush()
                        return
                    return self.reply(502,{'error':'Subscription transport failed'})
            def reply(self,status,data):
                encoded=json.dumps(data).encode();self.send_response(status)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)))
                self.end_headers();self.wfile.write(encoded)
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
    def record(self,receipt):
        with self.lock:
            with self.journal.open('a',encoding='utf8') as file:file.write(json.dumps(receipt)+'\n')
    def start(self):
        self.thread.start();return 'http://127.0.0.1:'+str(self.server.server_port)
    def close(self):
        self.server.shutdown();self.server.server_close();self.key=None
