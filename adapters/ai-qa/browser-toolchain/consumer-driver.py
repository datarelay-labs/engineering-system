"""Optional product observation probe; not a Full User E2E or release gate."""
import json,os,sys,subprocess,queue,threading,re,time,uuid,pathlib
from urllib.parse import urlsplit
if not __debug__:
    raise RuntimeError("Assertions must remain enabled")
if len(sys.argv)!=4:
    raise SystemExit("Usage: consumer-driver.py atlas|control|grant|foundation cli|mcp http://127.0.0.1:PORT")
product,mode,origin=sys.argv[1:4]
if product not in {"atlas","control","grant","foundation"} or mode not in {"cli","mcp"}:
    raise SystemExit("Unsupported product or browser driver")
u=urlsplit(origin)
if not (u.scheme=="http" and u.hostname=="127.0.0.1" and u.port and 1024<=u.port<=65535 and u.netloc==f"127.0.0.1:{u.port}" and not u.path and not u.query and not u.fragment):
    raise SystemExit("A fresh, unprivileged loopback fixture origin is required")
root=pathlib.Path(os.environ['HOME'])
exe=subprocess.check_output(['node','-e',"process.stdout.write(require('playwright').chromium.executablePath())"],cwd=root,text=True)
def wait(expression):
    return 'async () => {const end=Date.now()+12000;while(Date.now()<end){if('+expression+')return "PROBE_PASS";await new Promise(r=>setTimeout(r,100))}return "PROBE_FAIL"}'
def check(text):
    assert re.search(r'### Result\s+"?PROBE_PASS"?\s*(?:\n|$)',text),text[-2500:]
class CLI:
    def __init__(self):
        self.session='consumer-'+uuid.uuid4().hex[:8]
        self.config=pathlib.Path.cwd()/f'{self.session}.json'
        self.config.write_text(json.dumps({'browser':{'browserName':'chromium','launchOptions':{'headless':True,'chromiumSandbox':True,'executablePath':exe}},'network':{'allowedOrigins':[origin]}}))
    def run(self,*args):
        r=subprocess.run([str(root/'node_modules/.bin/playwright-cli'),'-s='+self.session,*args],capture_output=True,text=True,timeout=25)
        print(r.stdout[-1800:],r.stderr[-800:],flush=True)
        r.check_returncode()
        return r.stdout
    def open(self,path):self.run('open',origin+path,'--config='+str(self.config),'--idle-timeout=30000')
    def fill(self,selector,desc,value):self.run('fill',selector,value)
    def click(self,selector,desc):self.run('click',selector)
    def verify(self,expression):check(self.run('eval',wait(expression)))
    def close(self):
        try:self.run('close')
        finally:self.config.unlink(missing_ok=True)
class MCP:
    def __init__(self):
        self.err=open(product+'-mcp-stderr.log','w')
        self.proc=subprocess.Popen([str(root/'node_modules/.bin/playwright-mcp'),'--headless','--isolated','--sandbox','--browser','chrome','--executable-path',exe,'--allowed-origins',origin],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.err,text=True,bufsize=1)
        self.q=queue.Queue()
        def read():
            for line in self.proc.stdout:self.q.put(line)
            self.q.put(None)
        threading.Thread(target=read,daemon=True).start()
        self.seq=0
        self.rpc('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'consumer-probe','version':'1'}})
        self.send({'jsonrpc':'2.0','method':'notifications/initialized'})
        self.tools={t['name']:t['inputSchema'] for t in self.rpc('tools/list',{})['tools']}
    def send(self,msg):
        self.proc.stdin.write(json.dumps(msg)+'\n');self.proc.stdin.flush()
    def rpc(self,method,params):
        self.seq+=1;self.send({'jsonrpc':'2.0','id':self.seq,'method':method,'params':params})
        end=time.monotonic()+25
        while time.monotonic()<end:
            line=self.q.get(timeout=max(.1,end-time.monotonic()))
            if line is None:raise RuntimeError('MCP closed')
            r=json.loads(line)
            if r.get('id')==self.seq:
                if 'error' in r or r.get('result',{}).get('isError'):raise RuntimeError(str(r)[:3000])
                return r['result']
        raise TimeoutError(method)
    def call(self,name,args):
        r=self.rpc('tools/call',{'name':name,'arguments':{k:v for k,v in args.items() if k in self.tools[name].get('properties',{})}})
        s='\n'.join(c.get('text','') for c in r.get('content',[]));print(name,s[-1800:],flush=True);return s
    def open(self,path):self.call('browser_navigate',{'url':origin+path})
    def ref(self,desc):
        s=self.call('browser_snapshot',{})
        m=re.search(desc+r'[^\n]*\[ref=([^\]]+)\]',s)
        assert m,desc
        return m.group(1)
    def fill(self,selector,desc,value):
        ref=self.ref(desc);self.call('browser_type',{'target':ref,'ref':ref,'element':desc,'text':value})
    def click(self,selector,desc):
        ref=self.ref(desc);self.call('browser_click',{'target':ref,'ref':ref,'element':desc})
    def verify(self,expression):check(self.call('browser_evaluate',{'function':wait(expression)}))
    def close(self):
        self.proc.terminate()
        try:self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait()
        self.err.close()
d=CLI() if mode=='cli' else MCP()
try:
    if product=='atlas':
        d.open('/')
        d.verify('document.querySelector("h1")?.textContent==="Atlas Overview" && document.body.innerText.includes("<Demo & Co>")')
        d.click('a[href="/search"]',r'link "Search across projects')
        d.fill('input[name="q"]',r'textbox','needle')
        d.click('button[type="submit"]',r'button "Search"')
        d.verify('location.pathname==="/search" && new URL(location.href).searchParams.get("q")==="needle" && document.querySelector("article h3 a")?.textContent==="README.md" && document.body.innerText.includes("Unsafe <title>") && document.body.innerText.includes("canonical engineering source reference") && document.body.innerText.includes("readme@main") && document.body.innerText.includes("1 attributable result(s) across enabled projects") && document.body.innerText.includes("datarelay-labs/demo · main · README.md") && document.querySelectorAll("script").length===0')
    else:
        d.open('/settings' if product=='foundation' else '/')
        d.verify('document.querySelector("input[type=password]")!==null && [...document.querySelectorAll("button")].some(b=>/sign in|log in/i.test(b.textContent))')
    print(product.upper()+'_'+mode.upper()+'_PROBE_PASS',flush=True)
finally:d.close()
