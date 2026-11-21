#!/usr/bin/env python3
"""Fail-closed comparison of two standard StrykerJS mutation-testing reports.

Both runs must use exactly the same immutable source and mutant definitions.
The output is advisory and never certifies a product user-acceptance gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

HEX40 = re.compile(r"[a-f0-9]{40}\Z")
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
VALID = {"Killed", "Survived", "NoCoverage"}
UNQUALIFIED = {"TimedOut", "CompileError", "RuntimeError", "TranspileError",
               "Ignored", "Pending", "NotRun", "Static", "Suspicious"}
MAX_MUTANTS = 200


class Invalid(ValueError):
    """A missing, unverifiable or inconsistent mutation result."""


def need(ok: bool, reason: str) -> None:
    if not ok:
        raise Invalid(reason)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    need(0 < len(raw) <= 4_000_000, "OVERSIZED_REPORT")
    obj = json.loads(raw)
    need(type(obj) is dict and type(obj.get("files")) is dict, "INVALID_STRYKER_REPORT")
    return obj, digest(raw)


def extract(report: dict) -> dict[str, str]:
    mutants: dict[str, str] = {}
    for filename, file_data in report["files"].items():
        need(type(filename) is str and filename.endswith(".ts")
             and not filename.startswith("/") and ".." not in filename.split("/"),
             "INVALID_SOURCE_PATH")
        need(type(file_data) is dict and type(file_data.get("mutants")) is list,
             "INVALID_MUTANT_COLLECTION")
        for row in file_data["mutants"]:
            need(type(row) is dict, "INVALID_MUTANT")
            status = row.get("status")
            need(type(status) is str and status in (VALID | UNQUALIFIED),
                 "INVALID_STATUS")
            # All identity-defining fields must match; statuses may differ
            # only because the independent tests detect more faults.
            ident = {key: row.get(key) for key in
                     ("id", "mutatorName", "replacement", "location")}
            need(type(ident["id"]) is str
                 and type(ident["mutatorName"]) is str
                 and type(ident["replacement"]) is str
                 and type(ident["location"]) is dict,
                 "MISSING_MUTANT_IDENTITY")
            key = filename + ":" + json.dumps(ident, sort_keys=True)
            need(key not in mutants, "DUPLICATE_MUTANT")
            mutants[key] = status
    need(0 < len(mutants) <= MAX_MUTANTS, "EMPTY_OR_OVERSIZED_MUTANT_SET")
    return mutants


def compare(original: dict, enhanced: dict, *, head: str,
            source_sha256: str, report_hashes: tuple[str, str]) -> dict:
    need(bool(HEX40.fullmatch(head)) and bool(HEX64.fullmatch(source_sha256)),
         "INVALID_SOURCE_PROVENANCE")
    before = extract(original)
    after = extract(enhanced)
    need(set(before) == set(after), "DIFFERENT_MUTANT_SET")
    summary = {"Killed": 0, "Survived": 0, "NoCoverage": 0, "TimedOut": 0,
               "CompileError": 0, "RuntimeError": 0, "TranspileError": 0,
               "Ignored": 0, "Pending": 0, "NotRun": 0, "Static": 0, "Suspicious": 0}
    marginal = []
    denominator = 0
    killed_original = 0
    killed_enhanced = 0
    excluded = 0
    for name, first in sorted(before.items()):
        second = after[name]
        summary[first] += 1
        if first not in VALID or second not in VALID:
            excluded += 1
            continue
        need(not (first == "Killed" and second != "Killed"),
             "REGRESSION_KILLED_MUTANT_SURVIVED")
        denominator += 1
        if first == "Killed":
            killed_original += 1
        if second == "Killed":
            killed_enhanced += 1
        if first != "Killed" and second == "Killed":
            marginal.append(digest(name.encode()))
    need(denominator > 0, "NO_QUALIFIED_MUTANTS")
    return {
        "kind": "ai-qa-standard-stryker-same-mutant-comparison",
        "authority": "T0_ADVISORY_ONLY",
        "product_user_e2e_pass": False,
        "vendor_execution_verified": False,
        "head": head,
        "source_sha256": source_sha256,
        "original_report_sha256": report_hashes[0],
        "enhanced_report_sha256": report_hashes[1],
        "same_mutant_set": True,
        "attempted": len(before),
        "qualified": denominator,
        "not_qualified": excluded,
        "original_status_counts": summary,
        "original_killed": killed_original,
        "enhanced_killed": killed_enhanced,
        "independent_additional_kills": len(marginal),
        "independent_kill_fingerprints": marginal,
        "original_mutation_score": round(killed_original / denominator, 4),
        "enhanced_mutation_score": round(killed_enhanced / denominator, 4),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--original", type=Path, required=True)
    p.add_argument("--enhanced", type=Path, required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--source-sha256", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    try:
        need(not a.output.exists(), "REFUSE_EVIDENCE_OVERWRITE")
        original, first_hash = load(a.original)
        enhanced, second_hash = load(a.enhanced)
        report = compare(original, enhanced, head=a.head,
                         source_sha256=a.source_sha256,
                         report_hashes=(first_hash, second_hash))
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, sort_keys=True))
        return 0
    except (Invalid, OSError, UnicodeError, ValueError, TypeError) as exc:
        print(json.dumps({"authority": "T0_ADVISORY_ONLY", "status": "BLOCK",
                          "reason": str(exc) if isinstance(exc, Invalid)
                          else "INVALID_OR_UNREADABLE_REPORT"}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
