#!/usr/bin/env python3
import subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];T=ROOT/"tools/terminal_completion_notify.py"
def run(*a):return subprocess.run([sys.executable,str(T),*a],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
def main():
 h="a"*40
 assert run("--repository","bad","--workstream","w","--head",h,"--summary","x").returncode==3
 assert run("--repository","datarelay-labs/engineering-system","--workstream","bad space","--head",h,"--summary","x").returncode==3
 assert run("--repository","datarelay-labs/engineering-system","--workstream","w","--head","short","--summary","x").returncode==3
 src=T.read_text()
 for token in ("/usr/lib/engineering-system/telegram-complete-notify","/usr/bin/sudo","TERMINAL_TELEGRAM=PASS","COMPLETE"):assert token in src
 print("TERMINAL_COMPLETION_NOTIFY_TESTS=PASS")
if __name__=="__main__":main()
