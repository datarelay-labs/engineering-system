#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import hashlib
from handoff_contract import verify, verify_github_issue

BODY="""PACKET_VERSION=3
TARGET_REPO=datarelay-labs/demo
WORKSTREAM=demo
STATUS=ACTIVE
BRANCH=fix/demo
TASK_KIND=DEVELOPMENT
OWNER_INTENT=Continue the roadmap.
INTENT_REVISION=1
CHANGE_RISK=LOW
EXECUTION_PROFILE=datarelay-managed
EXECUTION_PROFILE_REVISION=3

## Goal
Finish roadmap.
## Current State
Runnable.
## Next Action
Continue implementation.
## Blockers
None.
"""

def facts(body=BODY,digest=None,token="Demo 계속"):
    return {"target_repo":"datarelay-labs/demo","packet_body":body,
            "persisted_body_sha256":digest or hashlib.sha256(body.encode()).hexdigest(),
            "continuation_token":token}

def test_documented_cli_flag_matches_parser():
    session=(Path(__file__).resolve().parents[1]/"standards"/"SESSION_CONTINUITY.md").read_text()
    assert "handoff_contract.py --request-json" not in session
    assert "handoff_contract.py --facts <facts.json>" in session
    assert "Freshly re-read the authoritative ACTIVE Work Packet" not in session
    assert "accurate canonical lifecycle state" in session


def test_strict_handoff_snapshot():
    """A locally self-consistent hash or next-chat comment cannot replace Issue persistence."""
    head = "0123456789abcdef0123456789abcdef01234567"
    first = "1. Finish the already-started inbox task and its focused tests."
    body = BODY.replace(
        "OWNER_INTENT=Continue the roadmap.",
        "OWNER_INTENT=Finish existing inbox task before selecting unrelated roadmap work.\n"
        f"LAST_VERIFIED_HEAD={head}",
    ).replace(
        "## Next Action\nContinue implementation.",
        "## Next Action\n" + first + "\n2. Only then select the next roadmap workstream.",
    )
    data = facts(body=body, token="Demo 계속 — Work Packet #7")
    data.update(expected_workstream="demo",
                expected_owner_intent="Finish existing inbox task before selecting unrelated roadmap work.",
                expected_intent_revision=1, verified_head=head, first_action=first)
    issue = {"number": 7, "state": "open",
             "html_url": "https://github.com/datarelay-labs/demo/issues/7", "body": body}
    assert verify_github_issue(data, 7, issue)["status"] == "PASS"
    # Old authoritative body still on GitHub; latest comment has the new instructions.
    old = {**issue, "body": BODY}
    assert verify_github_issue(data, 7, old)["reason"] == "PERSISTED_PACKET_MISMATCH"
    assert verify_github_issue({**data, "expected_intent_revision": 2}, 7, issue)["reason"] == "RESUME_ANCHOR_STALE"
    assert verify_github_issue({**data, "expected_owner_intent": "Analyze competitor UI now."}, 7, issue)["reason"] == "RESUME_ANCHOR_STALE"
    assert verify_github_issue({**data, "verified_head": "f"*40}, 7, issue)["reason"] == "RESUME_ANCHOR_STALE"
    assert verify_github_issue({**data, "first_action": "Recheck all issues."}, 7, issue)["reason"] == "FIRST_ACTION_NOT_PERSISTED"
    assert verify_github_issue({**data, "continuation_token": "Demo 계속"}, 7, issue)["reason"] == "CONTINUATION_TOKEN_NOT_BOUND"
    assert verify_github_issue(data, 9, issue)["reason"] == "GITHUB_ISSUE_IDENTITY_MISMATCH"
    assert verify_github_issue(data, 7, {**issue, "state": "closed"})["reason"] == "GITHUB_ISSUE_NOT_OPEN"
    assert verify_github_issue({k:v for k,v in data.items() if k!="first_action"}, 7, issue)["reason"] == "RESUME_ANCHOR_MISSING"
    assert verify_github_issue(data, 7, {**issue, "pull_request": {"url":"x"}})["reason"] == "GITHUB_ISSUE_IDENTITY_MISMATCH"
    blocked = body.replace("STATUS=ACTIVE", "STATUS=PAUSED")
    assert verify_github_issue(facts_with_body(data, blocked), 7, {**issue,"body":blocked})["status"] == "PASS"


def facts_with_body(data, body):
    return {**data, "packet_body": body, "persisted_body_sha256": hashlib.sha256(body.encode()).hexdigest()}

def main():
    test_documented_cli_flag_matches_parser()
    test_strict_handoff_snapshot()
    assert verify(facts())["status"]=="PASS"
    for state in ("PAUSED", "BLOCKED", "COMPLETE"):
        body=BODY.replace("STATUS=ACTIVE", "STATUS="+state)
        assert verify(facts(body=body))["status"]=="PASS"
    body=BODY.replace("STATUS=ACTIVE", "STATUS=WAITING")
    assert verify(facts(body=body))["reason"]=="PACKET_NOT_CLEAN"
    blocked=BODY.replace("None.", "Waiting for owner approval.")
    assert verify(facts(body=blocked))["reason"]=="ACTIVE_PACKET_NOT_RUNNABLE"
    for state in ("PAUSED","BLOCKED","COMPLETE"):
        assert verify(facts(body=blocked.replace("STATUS=ACTIVE","STATUS="+state)))["status"]=="PASS"
    waiting=BODY.replace("STATUS=ACTIVE","STATUS=ACTIVE"+chr(10)+"QUEUE_STATE=DEPENDENCY_WAIT")
    assert verify(facts(body=waiting))["reason"]=="ACTIVE_PACKET_NOT_RUNNABLE"
    for marker in ("WAITING_FOR_CI", "WAITING_FOR_CI=pending-head", "- WAITING_FOR_DEPENDENCY=core"):
        body=BODY+"## Latest Evidence"+chr(10)+marker+chr(10)
        assert verify(facts(body=body))["reason"]=="ACTIVE_PACKET_NOT_RUNNABLE"
        assert verify(facts(body=body.replace("STATUS=ACTIVE","STATUS=PAUSED")))["status"]=="PASS"
    body=BODY+"## Latest Evidence"+chr(10)+"WAITING_FOR_CI=PASS"+chr(10)
    assert verify(facts(body=body))["status"]=="PASS"
    body=BODY.replace("STATUS=ACTIVE","STATUS=ACTIVE"+chr(10)+"WAITING_FOR=CI")
    assert verify(facts(body=body))["reason"]=="ACTIVE_PACKET_NOT_RUNNABLE"
    body=BODY.replace("STATUS=ACTIVE","STATUS=ACTIVE"+chr(10)+"WAITING_FOR=NONE")
    assert verify(facts(body=body))["status"]=="PASS"
    for field, value in (("WAITING_FOR","NONE"),("WAITING_FOR","NO_WAIT"),("QUEUE_STATE","NOT_WAITING")):
        body=BODY.replace("STATUS=ACTIVE","STATUS=ACTIVE"+chr(10)+field+"="+value)
        assert verify(facts(body=body))["status"]=="PASS"
    for action in ("", "NONE", "N/A", "NONE."):
        body=BODY.replace("Continue implementation.",action)
        assert verify(facts(body=body))["status"]=="BLOCK"
    for fence in (chr(96)*3, "~"*3, chr(96)*4):
        quoted=fence+"text"+chr(10)+"WAITING_FOR_CI=past-head"+chr(10)+fence+chr(10)
        body=BODY+"## Latest Evidence"+chr(10)+quoted
        assert verify(facts(body=body))["status"]=="PASS"
        body+="WAITING_FOR_CI=current-head"+chr(10)
        assert verify(facts(body=body))["reason"]=="ACTIVE_PACKET_NOT_RUNNABLE"
    body=BODY.replace("## Current State","## Current State"+chr(10)+"- WAITING_FOR_CI",1)
    assert verify(facts(body=body))["reason"]=="ACTIVE_PACKET_NOT_RUNNABLE"
    assert verify(facts(body=body.replace("STATUS=ACTIVE","STATUS=PAUSED")))["status"]=="PASS"
    body=BODY.replace("## Current State","## Current State"+chr(10)+chr(96)*3+chr(10)+"WAITING_FOR_CI"+chr(10)+chr(96)*3,1)
    assert verify(facts(body=body))["status"]=="PASS"
    assert verify(facts(digest="0"*64))["reason"]=="PERSISTED_PACKET_MISMATCH"
    assert verify(facts(token="prompt\nwith extra instructions"))["reason"]=="CONTINUATION_TOKEN_INVALID"
    wrong=BODY.replace("datarelay-labs/demo","datarelay-labs/other")
    assert verify(facts(body=wrong,digest=hashlib.sha256(wrong.encode()).hexdigest()))["reason"]=="PACKET_NOT_CLEAN"
    print("HANDOFF_CONTRACT_TESTS=PASS")
    return 0
if __name__=="__main__":
    raise SystemExit(main())
