#!/usr/bin/env python3
"""Provider-neutral contract for bounded Atlas JIT context and derived write-back."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

ALLOWED_CANDIDATES={"OWNER_PREFERENCE","VALIDATED_FINDING","LESSON_LEARNED","RUN_SUMMARY","FUTURE_IDEA","REFERENCE_FACT"}
SAFE=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SECRET=re.compile(r"(?i)(authorization\s*:|bearer\s+[A-Za-z0-9._-]{8,}|password\s*=|api[_-]?key\s*=|client[_-]?secret\s*=|private[_-]?key)")

def ident(v,name):
 if not isinstance(v,str) or not SAFE.fullmatch(v) or ".." in Path(v).parts: raise ValueError(f"invalid {name}")
 return v

def request(args):
 out={"contract_version":1,"authority":"DERIVED_NON_AUTHORITATIVE","provider":"DATARELAY_ATLAS",
      "fallback":"CANONICAL_LOCAL_CONTEXT","project_id":ident(args.project_id,"project_id"),
      "repository":ident(args.repository,"repository"),"workstream":ident(args.workstream,"workstream") if args.workstream else None,
      "task":args.task}
 if not isinstance(args.task,str) or not args.task or len(args.task)>512 or SECRET.search(args.task):raise ValueError("unsafe task")
 print(json.dumps(out,sort_keys=True));return 0

def candidate(args):
 content=args.content
 if args.candidate_class not in ALLOWED_CANDIDATES:raise ValueError("invalid candidate class")
 if not content or len(content)>2048 or SECRET.search(content):raise ValueError("unsafe candidate content")
 out={"contract_version":1,"authority":"NON_AUTHORITATIVE_CANDIDATE","canonical":False,
      "project_id":ident(args.project_id,"project_id"),"repository":ident(args.repository,"repository"),
      "workstream":ident(args.workstream,"workstream") if args.workstream else None,
      "candidate_class":args.candidate_class,"content":content}
 print(json.dumps(out,sort_keys=True));return 0

def observation(args):
 vals=[args.important_expected,args.important_recalled,args.stale_injected,args.irrelevant_injected,args.duplicate_injected,args.injected_context_bytes,args.repeated_owner_explanations]
 if any(v<0 for v in vals) or args.important_recalled>args.important_expected:raise ValueError("invalid effectiveness observation")
 out={"contract_version":1,"content_free":True,"policy_mutated":False,
      "project_id":ident(args.project_id,"project_id"),"repository":ident(args.repository,"repository"),
      "workstream":ident(args.workstream,"workstream"),
      "important_expected":args.important_expected,"important_recalled":args.important_recalled,
      "stale_injected":args.stale_injected,"irrelevant_injected":args.irrelevant_injected,"duplicate_injected":args.duplicate_injected,
      "injected_context_bytes":args.injected_context_bytes,"repeated_owner_explanations":args.repeated_owner_explanations,
      "first_pass_success":args.first_pass_success}
 print(json.dumps(out,sort_keys=True));return 0

def parser():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest="cmd",required=True)
 r=s.add_parser("request");r.add_argument("--project-id",required=True);r.add_argument("--repository",required=True);r.add_argument("--workstream");r.add_argument("--task",required=True);r.set_defaults(func=request)
 c=s.add_parser("candidate");c.add_argument("--project-id",required=True);c.add_argument("--repository",required=True);c.add_argument("--workstream");c.add_argument("--candidate-class",required=True);c.add_argument("--content",required=True);c.set_defaults(func=candidate)
 o=s.add_parser("observation");o.add_argument("--project-id",required=True);o.add_argument("--repository",required=True);o.add_argument("--workstream",required=True)
 for n in ("important-expected","important-recalled","stale-injected","irrelevant-injected","duplicate-injected","injected-context-bytes","repeated-owner-explanations"):o.add_argument("--"+n,type=int,required=True)
 o.add_argument("--first-pass-success",action="store_true");o.set_defaults(func=observation);return p
def main():
 try:
  args=parser().parse_args()
  return args.func(args)
 except ValueError as e:print(f"ATLAS_CONTEXT_CONTRACT=BLOCK reason={e}");return 3
if __name__=="__main__":raise SystemExit(main())
