#!/usr/bin/env python3
"""Root host coordinator for one exact production-write assertion issuance."""
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path
import trusted_external_write_coordinator as ext

SIGNER=Path("/usr/lib/engineering-system/trusted-production-write-signer")
class Error(Exception):pass

def authorize(a):
    gh=ext._exec_path(ext.TRUSTED_GH,None); signer=ext._exec_path(SIGNER,None)
    issue=ext._run_json(gh,["api",f"repos/{a.repository}/issues/{a.issue_id}"])
    if not isinstance(issue,dict) or str(issue.get("state","")).lower()!="open" or not str(issue.get("title","")).startswith("[AI Work]"):raise Error("active Work Packet unavailable")
    body=issue.get("body");user=issue.get("user")
    if not isinstance(body,str) or not isinstance(user,dict):raise Error("Work Packet authority unavailable")
    login=str(user.get("login") or "");perm=ext._run_json(gh,["api",f"repos/{a.repository}/collaborators/{login}/permission"])
    permission=str(perm.get("permission") or "").lower() if isinstance(perm,dict) else ""
    if permission not in {"write","maintain","admin"}:raise Error("Work Packet author permission insufficient")
    f=ext._fields(body)
    required={"TARGET_REPO":a.repository,"WORKSTREAM":a.workstream,"STATUS":"ACTIVE","BRANCH":a.branch,
              "LAST_VERIFIED_HEAD":a.subject_head,"INTENT_REVISION":str(a.intent_revision)}
    for k,v in required.items():
        if f.get(k)!=v:raise Error(f"authoritative Work Packet mismatch: {k}")
    if f.get("CHANGE_RISK") not in {"HIGH","CRITICAL"}:raise Error("production write requires HIGH or CRITICAL risk")
    branch=ext._run_json(gh,["api",f"repos/{a.repository}/commits/{a.branch}"])
    if not isinstance(branch,dict) or str(branch.get("sha") or "").lower()!=a.subject_head:raise Error("authoritative branch HEAD mismatch")
    ext._canonical_execution_profile(gh,a.repository,a.subject_head)
    cmd=[str(signer),"--root",str(a.root),"--request-json",str(a.request_json),"--repository",a.repository,
         "--workstream",a.workstream,"--branch",a.branch,"--subject-head",a.subject_head,"--intent-revision",str(a.intent_revision),
         "--session-id",a.session_id,"--dispatch-id",a.dispatch_id,"--authority-permission",permission,"--ttl-seconds",str(a.ttl_seconds),
         "--binding-out",str(a.binding_out),"--dispatch-out",str(a.dispatch_out)]
    cp=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    if cp.returncode:raise Error("trusted production signer denied issuance: "+cp.stdout.strip())
    print("TRUSTED_PRODUCTION_WRITE_COORDINATOR=PASS");print(f"AUTHOR={login}");print(f"AUTHOR_PERMISSION={permission}");return 0
def parser():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ("repository","issue-id","workstream","branch","subject-head","session-id","dispatch-id"):p.add_argument("--"+n,required=True)
    p.add_argument("--root",type=Path,required=True);p.add_argument("--request-json",type=Path,required=True);p.add_argument("--intent-revision",type=int,required=True)
    p.add_argument("--ttl-seconds",type=int,default=120);p.add_argument("--binding-out",type=Path,required=True);p.add_argument("--dispatch-out",type=Path,required=True);return p
def main():
    try:return authorize(parser().parse_args())
    except (Error,ext.CoordinatorError,OSError) as e:print(f"TRUSTED_PRODUCTION_WRITE_COORDINATOR=BLOCK reason={e}");return 3
if __name__=="__main__":raise SystemExit(main())
