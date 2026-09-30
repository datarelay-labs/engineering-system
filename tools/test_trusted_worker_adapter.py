#!/usr/bin/env python3
import subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];T=ROOT/"tools/trusted_worker_adapter.py"
def main():
 cp=subprocess.run([sys.executable,str(T),"evaluate","--request-json",str(ROOT/"missing.json")],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 assert cp.returncode==3
 assert "root_required" in cp.stdout
 src=T.read_text()
 for x in ("/usr/lib/engineering-system/worker-adapter","/usr/bin/python3","choices=(\"evaluate\",)"):assert x in src
 print("TRUSTED_WORKER_ADAPTER_TESTS=PASS")
if __name__=="__main__":main()
