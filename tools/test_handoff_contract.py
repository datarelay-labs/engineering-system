#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import hashlib
from handoff_contract import verify

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

def main():
    test_documented_cli_flag_matches_parser()
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
    assert verify(facts(digest="0"*64))["reason"]=="PERSISTED_PACKET_MISMATCH"
    assert verify(facts(token="prompt\nwith extra instructions"))["reason"]=="CONTINUATION_TOKEN_INVALID"
    wrong=BODY.replace("datarelay-labs/demo","datarelay-labs/other")
    assert verify(facts(body=wrong,digest=hashlib.sha256(wrong.encode()).hexdigest()))["reason"]=="PACKET_NOT_CLEAN"
    print("HANDOFF_CONTRACT_TESTS=PASS")
    return 0
if __name__=="__main__":
    raise SystemExit(main())
