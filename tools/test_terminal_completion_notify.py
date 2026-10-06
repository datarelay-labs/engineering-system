#!/usr/bin/env python3
import subprocess,sys
from pathlib import Path
from terminal_completion_notify import success_marker_present
ROOT=Path(__file__).resolve().parents[1];T=ROOT/"tools/terminal_completion_notify.py"
def run(*a):return subprocess.run([sys.executable,str(T),*a],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
def main():
 h="a"*40
 bad=run("--repository","bad","--workstream","w","--head",h,"--summary","x"); assert bad.returncode==3; assert "OWNER_NOTIFICATION=RETRY_PENDING reason=identity" in bad.stdout
 assert run("--repository","datarelay-labs/engineering-system","--workstream","bad space","--head",h,"--summary","x").returncode==3
 assert run("--repository","datarelay-labs/engineering-system","--workstream","w","--head","short","--summary","x").returncode==3
 assert success_marker_present("OWNER_NOTIFY=PASS\n")
 assert not success_marker_present("OWNER_NOTIFY=PASSIVE\nOWNER_NOTIFY_RECEIPT=x\n")
 src=T.read_text()
 for token in ("/usr/lib/engineering-system/owner-notify","/usr/bin/sudo","OWNER_NOTIFICATION=RETRY_PENDING","OWNER_NOTIFICATION=PASS","OWNER_NOTIFICATION_RECEIPT=PASS","OWNER_NOTIFY=PASS","COMPLETE"):assert token in src
 print("TERMINAL_COMPLETION_NOTIFY_TESTS=PASS")
if __name__=="__main__":main()
