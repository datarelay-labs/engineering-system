#!/usr/bin/env python3
from lifecycle_audit import audit

def pkt(ws,status="ACTIVE",repo="datarelay-labs/product"):
 return f'''PACKET_VERSION=3\nTARGET_REPO={repo}\nWORKSTREAM={ws}\nSTATUS={status}\nBRANCH=feature/{ws}\nTASK_KIND=FEATURE\nOWNER_INTENT=Do work.\nINTENT_REVISION=1\nCHANGE_RISK=LOW\nEXECUTION_PROFILE=datarelay-managed\nEXECUTION_PROFILE_REVISION=3\n\n## Goal\nG\n## Current State\nS\n## Next Action\nN\n## Blockers\nNONE\n'''
def test_clean():
 r=audit({"issues":[{"number":1,"state":"OPEN","body":pkt("a")},{"number":2,"state":"OPEN","body":pkt("b","PAUSED")} ]}); assert r["status"]=="PASS" and r["summary"]["active_packets"]==[1]
def test_stale_complete():
 r=audit({"issues":[{"number":3,"state":"OPEN","body":pkt("done","COMPLETE")} ]}); assert r["status"]=="RECONCILE" and r["summary"]["open_complete"]==[3]
def test_closed_active():
 r=audit({"issues":[{"number":4,"state":"CLOSED","body":pkt("x")} ]}); assert r["summary"]["contradictions"]==[4]
def test_duplicate():
 r=audit({"issues":[{"number":5,"state":"OPEN","body":pkt("x")},{"number":6,"state":"OPEN","body":pkt("x","PAUSED")} ]}); assert r["summary"]["duplicate_candidates"]==[6]
def test_nonpacket():
 r=audit({"issues":[{"number":7,"state":"OPEN","body":"roadmap prose"}]}); assert r["status"]=="PASS" and r["issues"][0]["classification"]=="NOT_PACKET"
if __name__=='__main__':
 for f in (test_clean,test_stale_complete,test_closed_active,test_duplicate,test_nonpacket): f()
 print('LIFECYCLE_AUDIT_TESTS=PASS')
