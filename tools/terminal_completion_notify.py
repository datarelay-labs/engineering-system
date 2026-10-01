#!/usr/bin/env python3
from __future__ import annotations
import argparse,re,stat,subprocess
from pathlib import Path
HELPER=Path("/usr/lib/engineering-system/telegram-complete-notify")
SUDO=Path("/usr/bin/sudo")
SHA=re.compile(r"^[0-9a-f]{40}$")
REPO=re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKSTREAM=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
def trusted(path):
    try:
        st=path.lstat(); parent=path.parent.lstat()
        return stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode) and st.st_uid==0 and not(st.st_mode&0o022) and parent.st_uid==0 and not(parent.st_mode&0o022)
    except OSError:return False
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--repository",required=True);ap.add_argument("--workstream",required=True);ap.add_argument("--head",required=True);ap.add_argument("--summary",required=True);ap.add_argument("--status",choices=("COMPLETE","BLOCKED"),default="COMPLETE");a=ap.parse_args()
    if REPO.fullmatch(a.repository) is None or WORKSTREAM.fullmatch(a.workstream) is None or SHA.fullmatch(a.head) is None:
        print("OWNER_NOTIFICATION=RETRY_PENDING reason=identity");return 3
    if not a.summary.strip() or len(a.summary)>1000 or "\\x00" in a.summary:
        print("OWNER_NOTIFICATION=RETRY_PENDING reason=summary");return 3
    try:
        actual_head=subprocess.check_output(["/usr/bin/git","rev-parse","HEAD"],text=True,stderr=subprocess.DEVNULL).strip()
        origin=subprocess.check_output(["/usr/bin/git","remote","get-url","origin"],text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError):
        print("OWNER_NOTIFICATION=RETRY_PENDING reason=checkout");return 3
    expected_repo=a.repository
    accepted={f"https://github.com/{expected_repo}",f"https://github.com/{expected_repo}.git",f"git@github.com:{expected_repo}",f"git@github.com:{expected_repo}.git"}
    if actual_head!=a.head or origin not in accepted:
        print("OWNER_NOTIFICATION=RETRY_PENDING reason=stale_identity");return 3
    if not trusted(HELPER) or not trusted(SUDO):
        print("OWNER_NOTIFICATION=RETRY_PENDING reason=trusted_helper");return 3
    msg=f"Repository: {a.repository}\\nWorkstream: {a.workstream}\\nHEAD: {a.head}\\n{a.summary.strip()}"
    notify_type="COMPLETE" if a.status=="COMPLETE" else "ERROR"
    try: cp=subprocess.run([str(SUDO),"-n",str(HELPER),notify_type,msg],cwd="/",env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C"},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=20)
    except (OSError,subprocess.TimeoutExpired):
        print("OWNER_NOTIFICATION=RETRY_PENDING reason=delivery_ambiguous");return 3
    if cp.returncode!=0 or "TELEGRAM_SEND=PASS" not in cp.stdout:
        print("OWNER_NOTIFICATION=RETRY_PENDING reason=delivery_unverified");return 3
    print("OWNER_NOTIFICATION=PASS TERMINAL_TELEGRAM=PASS status="+a.status);return 0
if __name__=="__main__":raise SystemExit(main())
