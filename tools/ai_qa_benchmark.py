#!/usr/bin/env python3
"""Pinned, advisory-only blind QA benchmark. Never grants E2E or release authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
HEAD = re.compile(r"[0-9a-f]{40}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
REQUIRED_CHECKS = {"public_action", "final_outcome", "independent_oracle"}
SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
STATUSES = {"PASS", "FAIL", "BLOCKED", "UNVERIFIED"}
MAX_INPUT = 2 * 1024 * 1024
MAX_ARTIFACT = 8 * 1024 * 1024


class Invalid(Exception):
    """Input/provenance is malformed or contradictory; fail closed."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise Invalid(code)


def identifier(value: Any) -> str:
    require(isinstance(value, str) and bool(ID.fullmatch(value)), "MALFORMED_ID")
    return value


def strict_object(value: Any, required: set[str], optional: set[str] | None = None) -> dict:
    require(type(value) is dict, "MALFORMED_OBJECT")
    require(required <= value.keys(), "MISSING_FIELDS")
    require(value.keys() <= required | (optional or set()), "UNKNOWN_FIELDS")
    return value


def read_limited(path: Path, maximum: int = MAX_INPUT) -> bytes:
    require(path.is_file() and path.stat().st_size <= maximum, "MISSING_OR_OVERSIZED_INPUT")
    return path.read_bytes()


def document(path: Path) -> tuple[dict, bytes]:
    raw = read_limited(path)
    try:
        loaded = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise Invalid("INVALID_JSON") from exc
    require(type(loaded) is dict, "MALFORMED_DOCUMENT")
    return loaded, raw


def unique(items: Any, key: str, *, limit: int = 2000) -> dict[str, dict]:
    require(type(items) is list and len(items) <= limit, "INVALID_COLLECTION")
    found: dict[str, dict] = {}
    for item in items:
        require(type(item) is dict and key in item, "MALFORMED_ITEM")
        k = identifier(item[key])
        require(k not in found, "DUPLICATE_ID")
        found[k] = item
    return found


def check_artifact(artifact: Any, root: Path) -> bool:
    """Return False for missing proof; reject escape, symlinks and altered content."""
    strict_object(artifact, {"path", "sha256"})
    raw_path = artifact["path"]
    digest = artifact["sha256"]
    require(type(raw_path) is str and len(raw_path) <= 240, "UNSAFE_ARTIFACT_PATH")
    require(type(digest) is str and bool(SHA.fullmatch(digest)), "INVALID_ARTIFACT_DIGEST")
    rel = PurePosixPath(raw_path)
    require(
        not rel.is_absolute()
        and bool(rel.parts)
        and all(p not in ("", ".", "..") for p in raw_path.split("/"))
        and "\\" not in raw_path
        and ":" not in raw_path,
        "UNSAFE_ARTIFACT_PATH",
    )
    target = root.joinpath(*rel.parts)
    require(target.resolve().is_relative_to(root.resolve()), "ARTIFACT_OUTSIDE_ROOT")
    current = root
    for part in rel.parts:
        current = current / part
        require(not current.is_symlink(), "ARTIFACT_SYMLINK")
    if not target.is_file():
        return False
    raw = read_limited(target, MAX_ARTIFACT)
    require(hashlib.sha256(raw).hexdigest() == digest, "ALTERED_ARTIFACT")
    return True


def make_report(status: str, *, subject_head: str = "", plan_digest: str = "",
                metrics: dict | None = None, findings: dict | None = None,
                reasons: list[str] | None = None) -> dict:
    return {
        "kind": "independent-ai-qa-benchmark",
        "authority": "T0_ADVISORY_ONLY",
        "release_authorized": False,
        "human_equivalent_e2e_verified": False,
        "assessment": status,
        "subject_head": subject_head,
        "plan_sha256": plan_digest,
        "metrics": metrics or {},
        "findings": findings or {},
        "reasons": sorted(set(reasons or [])),
    }


def assess(plan: dict, observation: dict, artifacts_root: Path, *,
           expected_head: str, expected_plan_digest: str, actual_plan_digest: str) -> dict:
    require(bool(HEAD.fullmatch(expected_head)), "INVALID_EXPECTED_HEAD")
    require(bool(SHA.fullmatch(expected_plan_digest)), "INVALID_EXPECTED_PLAN_SHA")
    require(actual_plan_digest == expected_plan_digest, "UNPINNED_OR_CHANGED_ORACLE")
    strict_object(plan, {"schema_version", "kind", "target_repo", "subject_head",
                         "intent_revision", "plan_id", "journeys", "faults"})
    strict_object(observation, {"schema_version", "kind", "target_repo", "subject_head",
                                "intent_revision", "plan_id", "developer_actor", "qa_actor",
                                "journeys", "findings"})
    require(type(plan["schema_version"]) is int and type(observation["schema_version"]) is int
            and plan["schema_version"] == observation["schema_version"] == 1, "SCHEMA_VERSION")
    require(plan["kind"] == "ai-qa-holdout" and observation["kind"] == "ai-qa-observation", "KIND_MISMATCH")
    require(type(plan["target_repo"]) is str and bool(REPO.fullmatch(plan["target_repo"])), "BAD_REPO")
    for name in ("target_repo", "subject_head", "intent_revision", "plan_id"):
        require(plan[name] == observation[name], "SUBJECT_MISMATCH")
    require(plan["subject_head"] == expected_head, "STALE_OR_UNEXPECTED_HEAD")
    require(type(plan["intent_revision"]) is int and plan["intent_revision"] >= 1, "INVALID_REVISION")
    identifier(plan["plan_id"])

    def actor(value: Any) -> tuple[str, str]:
        strict_object(value, {"identity", "context_id"})
        return identifier(value["identity"]), identifier(value["context_id"])

    require(actor(observation["developer_actor"])[0] != actor(observation["qa_actor"])[0],
            "DECLARED_ACTOR_COLLISION")
    require(actor(observation["developer_actor"])[1] != actor(observation["qa_actor"])[1],
            "DECLARED_CONTEXT_COLLISION")

    planned = unique(plan["journeys"], "id")
    require(bool(planned), "EMPTY_JOURNEYS")
    faults = unique(plan["faults"], "id")
    for row in planned.values():
        strict_object(row, {"id", "required_checks"})
        checks = row["required_checks"]
        require(type(checks) is list and len(checks) <= 40, "BAD_REQUIRED_CHECKS")
        for item in checks:
            identifier(item)
        require(len(checks) == len(set(checks)), "BAD_REQUIRED_CHECKS")
        require(REQUIRED_CHECKS <= set(checks), "MISSING_INDEPENDENT_ORACLE")

    by_journey: dict[str, set[str]] = {k: set() for k in planned}
    for row in faults.values():
        strict_object(row, {"id", "journey_id", "severity"})
        require(type(row["journey_id"]) is str and row["journey_id"] in planned
                and type(row["severity"]) is str and row["severity"] in SEVERITIES, "INVALID_FAULT")
        by_journey[row["journey_id"]].add(row["id"])

    reports = unique(observation["journeys"], "id")
    alerts = unique(observation["findings"], "id")
    require(set(reports) <= set(planned), "UNKNOWN_JOURNEY")

    gaps: list[str] = []
    problems: list[str] = []
    detected: set[str] = set()
    unmatched: set[str] = set()
    reported_by_journey: dict[str, int] = {k: 0 for k in planned}
    for alert in alerts.values():
        strict_object(alert, {"id", "fault_id", "journey_id", "artifact"})
        identifier(alert["fault_id"])
        journey = identifier(alert["journey_id"])
        require(journey in planned, "UNKNOWN_FINDING_JOURNEY")
        if not check_artifact(alert["artifact"], artifacts_root):
            gaps.append("MISSING_FINDING_ARTIFACT:" + alert["id"])
            continue
        fault = faults.get(alert["fault_id"])
        if fault and fault["journey_id"] == journey:
            require(alert["fault_id"] not in detected, "DUPLICATED_FAULT_CLAIM")
            detected.add(alert["fault_id"])
        else:
            unmatched.add(alert["id"])
        reported_by_journey[journey] += 1

    for journey_id in planned:
        row = reports.get(journey_id)
        if row is None:
            gaps.append("MISSING_JOURNEY:" + journey_id)
            continue
        strict_object(row, {"id", "status", "checks"})
        status = row["status"]
        require(type(status) is str and status in STATUSES, "INVALID_JOURNEY_STATUS")
        checks = unique(row["checks"], "id", limit=40)
        required = set(planned[journey_id]["required_checks"])
        require(set(checks) <= required, "UNKNOWN_CHECK")
        for check_id in required - set(checks):
            gaps.append("MISSING_CHECK:" + journey_id + ":" + check_id)
        check_states: list[str] = []
        for check_id, check in checks.items():
            strict_object(check, {"id", "status"}, {"artifact"})
            require(type(check["status"]) is str and check["status"] in STATUSES, "INVALID_CHECK_STATUS")
            check_states.append(check["status"])
            if check["status"] in ("PASS", "FAIL"):
                if "artifact" not in check or not check_artifact(check["artifact"], artifacts_root):
                    gaps.append("MISSING_CHECK_ARTIFACT:" + journey_id + ":" + check_id)
            else:
                gaps.append("UNVERIFIED_CHECK:" + journey_id + ":" + check_id)
        if status in ("BLOCKED", "UNVERIFIED"):
            gaps.append("NONTERMINAL_JOURNEY:" + journey_id)
        elif status == "PASS":
            if any(s != "PASS" for s in check_states) or set(checks) != required:
                problems.append("INVALID_PASS_CHECKS:" + journey_id)
            if by_journey[journey_id]:
                problems.append("FALSE_PASS_JOURNEY:" + journey_id)
            if reported_by_journey[journey_id]:
                problems.append("PASS_HAS_FINDINGS:" + journey_id)
        else:  # FAIL
            if "FAIL" not in check_states:
                problems.append("FAIL_WITHOUT_FAILED_CHECK:" + journey_id)
            if not reported_by_journey[journey_id]:
                problems.append("UNATTRIBUTED_FAILURE:" + journey_id)

    missed = set(faults) - detected
    for journey_id, fault_ids in by_journey.items():
        if fault_ids and reports.get(journey_id, {}).get("status") == "PASS":
            problems.append("FALSE_PASS_JOURNEY:" + journey_id)
        if not fault_ids and reports.get(journey_id, {}).get("status") == "FAIL":
            problems.append("UNEXPECTED_CLEAN_FAILURE:" + journey_id)
    if unmatched:
        problems.append("UNMATCHED_FINDINGS_NEED_TRIAGE")

    high_critical = {k for k, f in faults.items() if f["severity"] in ("HIGH", "CRITICAL")}
    metrics = {
        "planned_journeys": len(planned),
        "observed_journeys": len(reports),
        "seeded_faults": len(faults),
        "detected_faults": len(detected),
        "missed_faults": len(missed),
        "detected_high_critical": len(high_critical & detected),
        "missed_high_critical": len(high_critical - detected),
        "fault_recall": round(len(detected) / len(faults), 4) if faults else None,
        "high_critical_recall": round(len(high_critical & detected) / len(high_critical), 4)
        if high_critical else None,
        "unmatched_findings": len(unmatched),
        "false_pass_journeys": len({p.split(":", 1)[1] for p in problems if p.startswith("FALSE_PASS_JOURNEY:")}),
        "coverage_fraction": round(len(reports) / len(planned), 4),
    }
    if gaps:
        assessment = "INCOMPLETE"
    elif problems or missed:
        assessment = "QUALITY_FAIL"
    else:
        assessment = "BENCHMARK_COMPLETE"
    return make_report(
        assessment,
        subject_head=expected_head,
        plan_digest=actual_plan_digest,
        metrics=metrics,
        findings={"detected_fault_ids": sorted(detected),
                  "missed_fault_ids": sorted(missed),
                  "unmatched_finding_ids": sorted(unmatched)},
        reasons=gaps + problems + (["SEE_MISSED_FAULT_IDS"] if missed else []),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a pinned independent AI QA campaign (T0/advisory only)")
    parser.add_argument("--plan", type=Path, required=True, help="Held-out, predeclared benchmark oracle JSON")
    parser.add_argument("--report", type=Path, required=True, help="Independent QA observation JSON")
    parser.add_argument("--artifacts-root", type=Path, required=True, help="Local evidence directory (read-only)")
    parser.add_argument("--expected-head", required=True, help="40-hex tested code HEAD")
    parser.add_argument("--expected-plan-sha256", required=True, help="Externally frozen 64-hex oracle digest")
    args = parser.parse_args(argv)
    try:
        plan, raw = document(args.plan)
        observation, _ = document(args.report)
        result = assess(plan, observation, args.artifacts_root, expected_head=args.expected_head,
                        expected_plan_digest=args.expected_plan_sha256,
                        actual_plan_digest=hashlib.sha256(raw).hexdigest())
    except (Invalid, OSError) as exc:
        code = str(exc) if isinstance(exc, Invalid) else "INPUT_IO_ERROR"
        result = make_report("BLOCK", reasons=[code])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {"BENCHMARK_COMPLETE": 0, "QUALITY_FAIL": 2, "INCOMPLETE": 2, "BLOCK": 3}[result["assessment"]]


if __name__ == "__main__":
    sys.exit(main())
