#!/usr/bin/env python3
"""Bounded Engineering System -> Atlas dogfood adapter.

Transport is an owner-provided executable argv prefix in ATLAS_OPERATOR_ARGV_JSON.
No endpoint/token/credential is stored in repository state.
"""
from __future__ import annotations
import argparse,json,os,subprocess,sys
from datetime import datetime,timezone

def _argv():
 raw=os.environ.get("ATLAS_OPERATOR_ARGV_JSON","")
 if not raw:return None
 try:x=json.loads(raw)
 except json.JSONDecodeError:raise ValueError("ATLAS_OPERATOR_ARGV_JSON invalid")
 if not isinstance(x,list) or not x or any(not isinstance(i,str) or not i for i in x):raise ValueError("ATLAS_OPERATOR_ARGV_JSON invalid")
 return x
def _run(extra, *, optional=False):
 base=_argv()
 if base is None:
  if optional:return {"state":"ATLAS_UNAVAILABLE","fallback":"CANONICAL_LOCAL_CONTEXT"}
  raise ValueError("Atlas operator transport unavailable")
 cp=subprocess.run([*base,*extra],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30,env={k:v for k,v in os.environ.items() if k!="ATLAS_OPERATOR_ARGV_JSON"})
 if cp.returncode:
  if optional:return {"state":"ATLAS_UNAVAILABLE","fallback":"CANONICAL_LOCAL_CONTEXT","exit_code":cp.returncode}
  raise ValueError("Atlas operator command failed")
 try:return json.loads(cp.stdout)
 except json.JSONDecodeError:raise ValueError("Atlas operator response invalid")
def now():return datetime.now(timezone.utc).isoformat()
def start(a):
 return _run(["task-context","show","--repository",a.repository,"--workstream",a.workstream],optional=True)
def finish(a):
 result={"candidate":{"state":"SKIPPED"},"effectiveness":{"state":"SKIPPED"}}
 if a.candidate_class and a.content:
  result["candidate"]=_run(["memory-candidates","ingest","--project-id",a.project_id,"--workstream",a.workstream,"--input-kind","RUN_SUMMARY","--observed-at",a.observed_at or now(),"--candidate-class",a.candidate_class,"--content",a.content],optional=True)
 obs=["memory-effectiveness","record","--project-id",a.project_id,"--repository",a.repository,"--workstream",a.workstream,"--observed-at",a.observed_at or now(),
 "--important-expected",str(a.important_expected),"--important-recalled",str(a.important_recalled),"--stale-injected",str(a.stale_injected),"--irrelevant-injected",str(a.irrelevant_injected),"--duplicate-injected",str(a.duplicate_injected),"--injected-context-bytes",str(a.injected_context_bytes),"--repeated-owner-explanations",str(a.repeated_owner_explanations)]
 if a.first_pass_success:obs.append("--first-pass-success")
 result["effectiveness"]=_run(obs,optional=True);return result
def parser():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest="cmd",required=True)
 st=s.add_parser("start");st.add_argument("--repository",required=True);st.add_argument("--workstream",required=True);st.set_defaults(func=start)
 f=s.add_parser("finish");f.add_argument("--project-id",required=True);f.add_argument("--repository",required=True);f.add_argument("--workstream",required=True);f.add_argument("--observed-at");f.add_argument("--candidate-class");f.add_argument("--content")
 for n in ("important-expected","important-recalled","stale-injected","irrelevant-injected","duplicate-injected","injected-context-bytes","repeated-owner-explanations"):f.add_argument("--"+n,type=int,default=0)
 f.add_argument("--first-pass-success",action="store_true");f.set_defaults(func=finish);return p
def main():
 try:
  a=parser().parse_args();print(json.dumps(a.func(a),sort_keys=True));return 0
 except (ValueError,subprocess.TimeoutExpired) as e:print(f"ATLAS_WORKFLOW=BLOCK reason={e}");return 3
if __name__=="__main__":raise SystemExit(main())
