import os, subprocess, socket, time, pathlib, json, urllib.request
p=pathlib.Path.cwd()
s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
server=subprocess.Popen(['node','fixture.cjs',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
cli=[str(p/'node_modules/.bin/playwright-cli'),'-s=es-smoke']
config=p/'cli.json'
config.write_text(json.dumps({'browser':{'browserName':'chromium','launchOptions':{'headless':True,'chromiumSandbox':True,'executablePath':subprocess.check_output(['node','-e',"process.stdout.write(require('playwright').chromium.executablePath())"],text=True)}}}))
def run(*args):
 r=subprocess.run(cli+list(args),capture_output=True,text=True,timeout=10);print(r.stdout,r.stderr,flush=True);r.check_returncode();return r.stdout
try:
 for attempt in range(50):
  try:
   with urllib.request.urlopen(f"http://127.0.0.1:{port}/",timeout=1) as response:
    if response.status==200: break
  except OSError: time.sleep(.1)
 else: raise RuntimeError("fixture not ready")
 run('open',f'http://127.0.0.1:{port}/','--config='+str(config),'--idle-timeout=10000')
 run('fill','#value','sentinel-20261125')
 run('click','button')
 out=run('eval',"() => document.querySelector('#result').textContent")
 assert '### Result\n"sentinel-20261125"\n' in out, out
 print('CLI_SMOKE_PASS',flush=True)
finally:
 try:run('close')
 finally:server.terminate();server.wait(timeout=5)
