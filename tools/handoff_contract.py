#!/usr/bin/env python3
"""Fail-closed next-chat handoff transaction verifier."""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path
from context_epoch import analyze_packet, parse_packet

SHA_RE = re.compile(r"^[0-9a-f]{64}$")

def verify(facts: dict) -> dict:
    required = ("target_repo","packet_body","persisted_body_sha256","continuation_token")
    missing=[k for k in required if not facts.get(k)]
    if missing:
        return {"status":"BLOCK","reason":"MISSING_FACTS","missing":missing}
    body=str(facts["packet_body"])
    digest=hashlib.sha256(body.encode()).hexdigest()
    persisted=str(facts["persisted_body_sha256"])
    if not SHA_RE.fullmatch(persisted) or digest != persisted:
        return {"status":"BLOCK","reason":"PERSISTED_PACKET_MISMATCH"}
    packet=parse_packet(body)
    lint=analyze_packet(packet, expected_target_repo=str(facts["target_repo"]))
    if lint["status"] != "PASS":
        return {
            "status":"BLOCK",
            "reason":"PACKET_NOT_CLEAN",
            "blocking":lint["blocking"],
            "warnings":lint["warnings"],
        }
    if packet.metadata.get("STATUS") == "ACTIVE":
        blockers=packet.sections.get("Blockers", "").strip().upper()
        waiting=any(
            "WAIT" in packet.metadata.get(key, "").upper()
            or packet.metadata.get(key, "").upper() in {"BLOCKED","DEFERRED","HUMAN_REQUIRED","PENDING"}
            for key in ("QUEUE_STATE","WAITING_FOR","DEPENDENCY_STATUS")
        )
        if blockers not in {"NONE","- NONE","NONE."} or waiting:
            return {"status":"BLOCK","reason":"ACTIVE_PACKET_NOT_RUNNABLE"}
    token=str(facts["continuation_token"]).strip()
    if not token or "\n" in token or len(token)>120:
        return {"status":"BLOCK","reason":"CONTINUATION_TOKEN_INVALID"}
    return {"status":"PASS","reason":"HANDOFF_TRANSACTION_VERIFIED","continuation_token":token}

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--facts",required=True)
    a=p.parse_args()
    try:
        facts=json.loads(Path(a.facts).read_text())
    except Exception:
        print(json.dumps({"status":"BLOCK","reason":"FACTS_INVALID"},sort_keys=True)); return 2
    result=verify(facts); print(json.dumps(result,sort_keys=True))
    return 0 if result["status"]=="PASS" else 2
if __name__=="__main__":
    raise SystemExit(main())
