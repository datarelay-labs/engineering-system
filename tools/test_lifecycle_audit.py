#!/usr/bin/env python3
from lifecycle_audit import audit, live_audit, scan_worktrees

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
def test_marked_complete_with_legacy_history():
 body=(
     "<!-- ENGINEERING_CURRENT_PACKET_BEGIN -->\n"
     + pkt("merged-source","COMPLETE")
     + "<!-- ENGINEERING_CURRENT_PACKET_END -->\n"
     + "## Historical work\nSTATUS=ACTIVE\n"
 )
 r=audit({"issues":[{"number":8,"state":"OPEN","body":body}]})
 assert r["summary"]["open_complete"]==[8],r

def test_completed_source_exact_pr_classification():
    head="a"*40
    body=pkt("match","COMPLETE").replace("BRANCH=feature/match", "BRANCH=feature/match"+chr(10)+"LAST_VERIFIED_HEAD="+head)
    issue={"number":9,"state":"OPEN","body":body}
    pull={"number":8,"state":"closed","merged_at":None,
          "head":{"ref":"feature/match","sha":head}}
    blocked=audit({"issues":[issue],"pull_requests":[pull]})
    assert blocked["summary"]["protected_complete"]==[9]
    assert not blocked["summary"]["close_candidates"]
    assert blocked["issues"][0]["classification"]=="OPEN_COMPLETE_CLOSED_UNMERGED"
    pull["merged_at"]="2026-11-23T00:00:00Z"
    approved=audit({"issues":[issue],"pull_requests":[pull]})
    assert approved["summary"]["close_candidates"]==[9]
    assert approved["issues"][0]["classification"]=="OPEN_COMPLETE_MERGED_SOURCE"
    assert approved["MUTATION_AUTHORITY"]=="NO"
    pull["state"]="open"
    pull["merged_at"]=None
    opened=audit({"issues":[issue],"pull_requests":[pull]})
    assert opened["issues"][0]["classification"]=="OPEN_COMPLETE_OPEN_PR"


def test_live_audit_injected_authenticated_source():
    head="b"*40
    body=pkt("done","COMPLETE").replace("BRANCH=feature/done", "BRANCH=feature/done"+chr(10)+"LAST_VERIFIED_HEAD="+head)
    seen=[]
    def api(path):
        seen.append(path)
        if "issues?state=open" in path:
            return [{"number":7,"state":"open","body":body}]
        if "pulls?state=all" in path:
            return [{"number":11,"state":"closed","merged_at":None,
                     "head":{"ref":"feature/done","sha":head}}]
        raise AssertionError(path)
    result, source=live_audit("datarelay-labs/engineering-system",api)
    assert result["summary"]["protected_complete"]==[7]
    assert result["open_pr_count"]==0 and len(source["pull_requests"])==1
    assert len(seen)==2 and all("page=1" in x for x in seen)
    assert result["MUTATION_AUTHORITY"]=="NO"


def test_disposable_worktree_scan_does_not_delete():
    import tempfile
    from test_worktree_lifecycle import Fixture, REPO
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d)
        pr={
          "number":999,"state":"closed","merged_at":"2026-11-23T00:00:00Z",
          "head":{"ref":"feat/cleanup","sha":f.head,
                  "repo":{"full_name":REPO}},
        }
        source={"pull_requests":[pr],"issues":[
          {"number":123,"body":f.issue["body"]},
        ]}
        referenced=scan_worktrees(f.root,REPO,source)
        assert len(referenced)==1
        assert referenced[0]["classification"]=="PRESERVE"
        assert referenced[0]["reason"]=="OPEN_ISSUE_REFERENCES"
        source["issues"]=[]
        safe=scan_worktrees(f.root,REPO,source)
        assert safe[0]["classification"]=="CANDIDATE_REVIEW"
        assert f.wt.exists(), "audit must not delete worktrees"


def test_nonpacket():
 r=audit({"issues":[{"number":7,"state":"OPEN","body":"roadmap prose"}]}); assert r["status"]=="PASS" and r["issues"][0]["classification"]=="NOT_PACKET"
if __name__=='__main__':
 for f in (test_clean,test_stale_complete,test_closed_active,test_duplicate,test_nonpacket,test_marked_complete_with_legacy_history,
           test_completed_source_exact_pr_classification,
           test_live_audit_injected_authenticated_source,
           test_disposable_worktree_scan_does_not_delete): f()
 print('LIFECYCLE_AUDIT_TESTS=PASS')
