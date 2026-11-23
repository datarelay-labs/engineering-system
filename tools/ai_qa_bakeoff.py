#!/usr/bin/env python3
"""Compare independently pinned QA tools on the SAME hidden fault challenge.

No network, external invocation, command execution, account access or release authority.
Measurement results are advisory; SaaS cost and invocation identity remain unverified.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ai_qa_benchmark", ROOT / "ai_qa_benchmark.py")
assert spec and spec.loader
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
SHA = re.compile(r"[a-f0-9]{64}\Z")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_file(root: Path, raw: str, maximum: int) -> tuple[Path, bytes]:
    base.require(type(raw) is str and 0 < len(raw) <= 240, "INVALID_PATH")
    base.require("\\" not in raw and ":" not in raw and not raw.startswith("/"), "INVALID_PATH")
    parts = raw.split("/")
    base.require(all(part not in ("", ".", "..") for part in parts), "INVALID_PATH")
    candidate = root.joinpath(*parts)
    base.require(candidate.resolve().is_relative_to(root.resolve()), "OUTSIDE_REPORTS_ROOT")
    item = root
    for part in parts:
        item = item / part
        base.require(not item.is_symlink(), "SYMLINK_REPORT_PATH")
    base.require(candidate.is_file() and candidate.stat().st_size <= maximum, "MISSING_OR_OVERSIZED_REPORT")
    return candidate, candidate.read_bytes()


def decoded(raw: bytes) -> dict:
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise base.Invalid("INVALID_JSON") from exc
    base.require(type(parsed) is dict, "MALFORMED_DOCUMENT")
    return parsed


def evaluate(holdout: dict, manifest: dict, *, holdout_raw: bytes,
             manifest_raw: bytes, reports_root: Path,
             expected_head: str, expected_holdout_sha: str, expected_manifest_sha: str) -> dict:
    base.require(bool(base.HEAD.fullmatch(expected_head)), "INVALID_EXPECTED_HEAD")
    base.require(decoded(holdout_raw) == holdout, "UNPINNED_HOLDOUT")
    base.require(decoded(manifest_raw) == manifest, "UNPINNED_CAMPAIGN")
    base.require(type(expected_holdout_sha) is str and bool(SHA.fullmatch(expected_holdout_sha))
                 and sha(holdout_raw) == expected_holdout_sha, "UNPINNED_HOLDOUT")
    base.require(type(expected_manifest_sha) is str and bool(SHA.fullmatch(expected_manifest_sha))
                 and sha(manifest_raw) == expected_manifest_sha, "UNPINNED_CAMPAIGN")
    base.strict_object(manifest, {"version", "kind", "target_repo", "subject_head", "holdout_sha256", "runs"})
    base.require(type(manifest["version"]) is int and manifest["version"] == 1
                 and manifest["kind"] == "ai-qa-provider-bakeoff", "INVALID_CAMPAIGN")
    base.require(manifest["subject_head"] == expected_head
                 and manifest["target_repo"] == holdout.get("target_repo")
                 and manifest["holdout_sha256"] == expected_holdout_sha
                 and holdout.get("subject_head") == expected_head, "DIFFERENT_CHALLENGE")
    runs = manifest["runs"]
    base.require(type(runs) is list and 2 <= len(runs) <= 12, "INVALID_RUN_COUNT")
    seen: set[str] = set()
    rows: list[dict] = []
    for item in runs:
        base.strict_object(item, {"id", "report", "report_sha256", "artifacts_dir",
                                  "declared_cost_usd", "declared_duration_seconds",
                                  "declared_invocations", "tool"})
        run_id, tool_name = item["id"], item["tool"]
        base.require(type(run_id) is str and bool(ID.fullmatch(run_id)) and run_id not in seen,
                     "DUPLICATE_INVALID_RUN")
        seen.add(run_id)
        base.require(type(tool_name) is str and bool(ID.fullmatch(tool_name)), "INVALID_TOOL_LABEL")
        base.require(type(item["report_sha256"]) is str and bool(SHA.fullmatch(item["report_sha256"])),
                     "INVALID_REPORT_PIN")
        base.require(type(item["declared_cost_usd"]) in (float, int)
                     and 0 <= item["declared_cost_usd"] <= 1_000_000, "INVALID_COST")
        base.require(type(item["declared_duration_seconds"]) in (float, int)
                     and 0 < item["declared_duration_seconds"] <= 86400 * 3, "INVALID_DURATION")
        base.require(type(item["declared_invocations"]) is int
                     and 0 <= item["declared_invocations"] <= 100_000, "INVALID_INVOCATIONS")
        report_file, data = safe_file(reports_root, item["report"], 2 * 1024 * 1024)
        base.require(sha(data) == item["report_sha256"], "REPORT_CHANGED_AFTER_FREEZE")
        relative = item["artifacts_dir"]
        base.require(type(relative) is str and 0 < len(relative) <= 240
                     and not relative.startswith("/") and "\\" not in relative and ":" not in relative
                     and all(p not in ("", ".", "..") for p in relative.split("/")), "UNSAFE_ARTIFACT_DIR")
        artifact_root = reports_root.joinpath(*relative.split("/"))
        base.require(artifact_root.resolve().is_relative_to(reports_root.resolve())
                     and artifact_root.is_dir() and not artifact_root.is_symlink(), "UNTRUSTED_ARTIFACT_DIR")
        # Exact-head and frozen-oracle verification is repeated PER runner; no cross-provider substitution.
        scored = base.assess(holdout, decoded(data), artifact_root,
                             expected_head=expected_head,
                             expected_plan_digest=expected_holdout_sha,
                             actual_plan_digest=sha(holdout_raw))
        m = scored["metrics"]
        hits = m.get("detected_faults", 0)
        rows.append({
            "id": run_id, "tool": tool_name, "assessment": scored["assessment"],
            "detected_faults": hits, "seeded_faults": m.get("seeded_faults", 0),
            "missed_high_critical": m.get("missed_high_critical", 0),
            "fault_recall": m.get("fault_recall"), "false_pass_journeys": m.get("false_pass_journeys", 0),
            "unmatched_findings_need_triage": m.get("unmatched_findings", 0),
            "declared_cost_usd": item["declared_cost_usd"],
            "declared_duration_seconds": item["declared_duration_seconds"],
            "declared_invocations": item["declared_invocations"],
            "declared_cost_per_seeded_hit_usd": round(item["declared_cost_usd"] / hits, 4) if hits else None,
            "report_sha256": sha(data),
            "report_file": report_file.relative_to(reports_root).as_posix(),
        })
    ordered = sorted(rows, key=lambda x: (
        x["assessment"] == "INCOMPLETE",
        x["missed_high_critical"],
        x["false_pass_journeys"],
        -x["detected_faults"],
        x["unmatched_findings_need_triage"],
        x["id"],
    ))
    return {
        "kind": "ai-qa-blind-provider-bakeoff",
        "authority": "T0_ADVISORY_ONLY",
        "product_acceptance": False,
        "vendor_execution_verified": False,
        "costs_independently_verified": False,
        "challenge_sha256": expected_holdout_sha,
        "campaign_sha256": expected_manifest_sha,
        "subject_head": expected_head,
        "run_count": len(ordered),
        "comparison_order_not_a_procurement_decision": [v["id"] for v in ordered],
        "results": ordered,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare frozen common held-out AI QA trial results (advisory)")
    ap.add_argument("--holdout", type=Path, required=True)
    ap.add_argument("--campaign", type=Path, required=True)
    ap.add_argument("--reports-root", type=Path, required=True)
    ap.add_argument("--expected-head", required=True)
    ap.add_argument("--expected-holdout-sha256", required=True)
    ap.add_argument("--expected-campaign-sha256", required=True)
    a = ap.parse_args()
    try:
        p, raw = base.document(a.holdout)
        c, campaign_raw = base.document(a.campaign)
        result = evaluate(p, c, holdout_raw=raw, manifest_raw=campaign_raw,
                          reports_root=a.reports_root, expected_head=a.expected_head,
                          expected_holdout_sha=a.expected_holdout_sha256,
                          expected_manifest_sha=a.expected_campaign_sha256)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (base.Invalid, OSError, ValueError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, base.Invalid) else "MALFORMED_OR_INACCESSIBLE_INPUT"
        print(json.dumps({"kind": "ai-qa-blind-provider-bakeoff", "assessment": "BLOCK",
                          "authority": "T0_ADVISORY_ONLY", "vendor_execution_verified": False,
                          "reason": reason}, sort_keys=True))
        return 3


if __name__ == "__main__":
    sys.exit(main())
