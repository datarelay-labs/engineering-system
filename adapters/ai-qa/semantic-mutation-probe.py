#!/usr/bin/env python3
"""Bounded evidence-level semantic mutants for an independently captured product E2E.

This deliberately changes copies of observations and re-hashes them. It measures
oracle strength against planted result defects, not source-code Stryker mutation.
No changes are made to applications, customer data, or the source observation.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("parser_studio_qa_oracle", ROOT / "parser-studio-oracle.py")
assert spec and spec.loader
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def mutate(record: dict, op) -> dict:
    updated = copy.deepcopy(record)
    backend = json.loads(updated["responses"][0]["body"])
    op(backend)
    raw = json.dumps(backend, separators=(",", ":"), ensure_ascii=False)
    updated["responses"][0]["body"] = raw
    updated["responses"][0]["sha256"] = sha(raw.encode())
    return updated


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--mission", required=True, type=Path)
    a.add_argument("--capture", required=True, type=Path)
    a.add_argument("--draft", required=True, type=Path)
    a.add_argument("--evidence-root", required=True, type=Path)
    a.add_argument("--expected-mission-sha256", required=True)
    a.add_argument("--out", required=True, type=Path)
    args = a.parse_args()
    if args.out.exists():
        raise SystemExit("FAIL refuse to overwrite mutation evidence")
    plan, raw = oracle.read_document(args.mission)
    base, _ = oracle.read_document(args.capture)
    draft, _ = oracle.read_document(args.draft)
    expected = dict(frozen_sha256=args.expected_mission_sha256, raw_mission=raw, root=args.evidence_root)
    oracle.assess(plan, base, draft, **expected)  # baseline must be independently green

    operations = [
        ("lose-an-event", lambda b: b["events"].pop()),
        ("false-event-count", lambda b: b.update(eventCount=1)),
        ("rewrite-an-action", lambda b: b["events"][0]["preview"]["fields"].update(action="INJECTED")),
        ("remove-a-source-field", lambda b: b["events"][1]["preview"]["fields"].pop("src_ip")),
        ("mutate-utf8-span", lambda b: b["events"][0]["preview"]["spans"][0].update(startByte=0)),
        ("mutate-record-offset", lambda b: b["events"][1].update(startByte=0)),
        ("mutate-record-sha", lambda b: b["events"][0]["preview"].update(sourceSha256="0" * 64)),
        ("false-parsed-status", lambda b: b["events"][0]["preview"].update(status="SOURCE_PARTIAL")),
        ("false-syntax", lambda b: b["events"][1]["preview"].update(syntax="CEF")),
        ("invent-residual", lambda b: b["events"][0]["preview"].update(residuals=[{"source":"unparsed"}])),
        ("false-native-verified", lambda b: b.update(nativeVerified=True)),
        ("false-target-verified", lambda b: b.update(targetVerified=True)),
    ]
    records = []
    for name, op in operations:
        changed = mutate(base, op)
        try:
            oracle.assess(plan, changed, draft, **expected)
            records.append({"mutant": name, "result": "SURVIVED"})
        except oracle.EvidenceError as exc:
            records.append({"mutant": name, "result": "KILLED", "reason": str(exc)})
    killed = sum(x["result"] == "KILLED" for x in records)
    result = {
        "kind": "evidence-level-semantic-mutation-probe",
        "authority": "T0_ADVISORY_ONLY",
        "source_code_mutation": False,
        "browser_evidence_sha256": sha(args.capture.read_bytes()),
        "frozen_mission_sha256": args.expected_mission_sha256,
        "mutants": len(records),
        "killed": killed,
        "survived": len(records) - killed,
        "score": round(killed / len(records), 4),
        "details": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "details"}, sort_keys=True))
    return 0 if killed == len(records) else 2


if __name__ == "__main__":
    sys.exit(main())
