import os, subprocess, socket, time, pathlib, json, urllib.request, select, re
p=pathlib.Path.cwd()
s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
server=subprocess.Popen(['node','fixture.cjs',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
err=open('mcp-stderr.log','w')
m=subprocess.Popen([str(p/'node_modules/.bin/playwright-mcp'),'--headless','--isolated','--sandbox','--browser','chrome','--executable-path',subprocess.check_output(['node','-e',"process.stdout.write(require('playwright').chromium.executablePath())"],text=True),'--allowed-origins',f'http://127.0.0.1:{port}'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,text=True,bufsize=1)
i=0
def send(msg):
 m.stdin.write(json.dumps(msg)+'\n');m.stdin.flush()
def rpc(method,params):
 global i
 i+=1
 send({'jsonrpc':'2.0','id':i,'method':method,'params':params})
 while True:
  if not select.select([m.stdout],[],[],12)[0]:raise TimeoutError(method)
  line=m.stdout.readline()
  if not line:raise RuntimeError('MCP closed')
  r=json.loads(line)
  if r.get('id')==i:
   if 'error' in r:raise RuntimeError(r)
   result=r['result']
   if result.get('isError'):raise RuntimeError(result)
   return result
def call(name,args):
 result=rpc('tools/call',{'name':name,'arguments':args})
 text='\n'.join(c.get('text','') for c in result.get('content',[]))
 print(name,text[:5000],flush=True)
 return text
try:
 for attempt in range(50):
  try:
   with urllib.request.urlopen(f"http://127.0.0.1:{port}/",timeout=1) as response:
    if response.status==200: break
  except OSError: time.sleep(.1)
 else: raise RuntimeError("fixture not ready")
 print(rpc('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'es-poc','version':'1'}}),flush=True)
 send({'jsonrpc':'2.0','method':'notifications/initialized'})
 tools=rpc('tools/list',{})['tools']
 print([x['name'] for x in tools],flush=True)
 for x in tools:
  if x['name'] in ['browser_type','browser_click','browser_fill_form','browser_snapshot','browser_evaluate']:print(x['name'],x['inputSchema'],flush=True)
 snap=call('browser_navigate',{'url':f'http://127.0.0.1:{port}/'})
 if 'textbox' not in snap:snap=call('browser_snapshot',{})
 field=re.search(r'textbox[^\n]*\[ref=([^\]]+)\]',snap).group(1)
 button=re.search(r'button "Submit"[^\n]*\[ref=([^\]]+)\]',snap).group(1)
 schemas={t['name']:t['inputSchema'] for t in tools}
 def args(name,values):
  return {k:v for k,v in values.items() if k in schemas[name].get('properties',{})}
 call('browser_type',args('browser_type',{'element':'Value','ref':field,'target':field,'text':'sentinel-20261125'}))
 call('browser_click',args('browser_click',{'element':'Submit','ref':button,'target':button}))
 out=call('browser_snapshot',{})
 assert re.search(r'status[^\n]*sentinel-20261125',out),out
 print('MCP_SMOKE_PASS',flush=True)
finally:
 m.terminate();m.wait(timeout=5);server.terminate();server.wait(timeout=5);err.close()
