#!/usr/bin/env python3
from __future__ import annotations
import hashlib
from handoff_contract import verify

BODY="""PACKET_VERSION=3
TARGET_REPO=datarelay-labs/demo
WORKSTREAM=demo
STATUS=ACTIVE
BRANCH=fix/demo
TASK_KIND=DEVELOPMENT
OWNER_INTENT=Continue the roadmap.
LAST_VERIFIED_HEAD=1111111111111111111111111111111111111111
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
            "continuation_token":token,
            "observed_branch":"fix/demo",
            "observed_head":"1111111111111111111111111111111111111111",
            "observed_workstream":"demo"}

def main():
    assert verify(facts())["status"]=="PASS"
    assert verify(facts(digest="0"*64))["reason"]=="PERSISTED_PACKET_MISMATCH"
    assert verify(facts(token="prompt\nwith extra instructions"))["reason"]=="CONTINUATION_TOKEN_INVALID"
    stale=facts(); stale["observed_head"]="2"*40
    assert verify(stale)["reason"]=="HEAD_MISMATCH"
    wrong=BODY.replace("datarelay-labs/demo","datarelay-labs/other")
    assert verify(facts(body=wrong,digest=hashlib.sha256(wrong.encode()).hexdigest()))["reason"]=="PACKET_NOT_CLEAN"
    print("HANDOFF_CONTRACT_TESTS=PASS")
    return 0
if __name__=="__main__":
    raise SystemExit(main())
