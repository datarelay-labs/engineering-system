#!/usr/bin/env python3
"""Read-only Work Packet lifecycle audit for product/supervisor reconciliation."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from context_epoch import parse_packet

TERMINAL={"COMPLETE"}; WAITING={"PAUSED","BLOCKED"}

def audit(payload):
    issues=payload.get("issues")
    if not isinstance(issues,list): raise ValueError("issues must be a list")
    rows=[]; active=[]; seen={}
    for raw in issues:
        if not isinstance(raw,dict): raise ValueError("issue must be an object")
        num=raw.get("number"); state=str(raw.get("state","")).upper(); body=raw.get("body","")
        if not isinstance(num,int) or state not in {"OPEN","CLOSED"} or not isinstance(body,str): raise ValueError("invalid issue facts")
        try: p=parse_packet(body)
        except Exception:
            rows.append({"number":num,"classification":"NOT_PACKET"}); continue
        if "PACKET_VERSION" not in p.metadata:
            rows.append({"number":num,"classification":"NOT_PACKET"}); continue
        status=p.metadata.get("STATUS",""); ws=p.metadata.get("WORKSTREAM",""); branch=p.metadata.get("BRANCH","")
        key=(p.metadata.get("TARGET_REPO",""),ws)
        cls="HEALTHY"
        if state=="OPEN" and status in TERMINAL: cls="STALE_OPEN_COMPLETE"
        elif state=="CLOSED" and status=="ACTIVE": cls="CLOSED_ACTIVE_CONTRADICTION"
        elif state=="OPEN" and status=="ACTIVE": active.append(num)
        elif state=="OPEN" and status in WAITING: cls="HEALTHY_DEFERRED"
        if key in seen and ws: cls="DUPLICATE_WORKSTREAM_CANDIDATE"
        elif ws: seen[key]=num
        rows.append({"number":num,"classification":cls,"status":status,"workstream":ws,"branch":branch})
    summary={"open_complete":[r["number"] for r in rows if r["classification"]=="STALE_OPEN_COMPLETE"],
             "contradictions":[r["number"] for r in rows if r["classification"]=="CLOSED_ACTIVE_CONTRADICTION"],
             "duplicate_candidates":[r["number"] for r in rows if r["classification"]=="DUPLICATE_WORKSTREAM_CANDIDATE"],
             "active_packets":active}
    summary["needs_reconciliation"]=any(summary[k] for k in ("open_complete","contradictions","duplicate_candidates"))
    return {"status":"RECONCILE" if summary["needs_reconciliation"] else "PASS","summary":summary,"issues":rows}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--facts",required=True); a=ap.parse_args()
    try: data=json.loads(Path(a.facts).read_text()); out=audit(data)
    except (OSError,json.JSONDecodeError,ValueError) as e: print(json.dumps({"status":"BLOCK","reason":str(e)},sort_keys=True)); return 3
    print(json.dumps(out,sort_keys=True)); return 2 if out["status"]=="RECONCILE" else 0
if __name__=="__main__": raise SystemExit(main())
