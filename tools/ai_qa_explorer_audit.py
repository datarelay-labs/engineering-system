#!/usr/bin/env python3
"""Independent read-only audit of bounded AI-chosen Chromium exploration receipts.

This checks provenance *consistency*, not the claimed runtime actor's identity,
LLM independence, the semantic truth of user oracles, or a Full User E2E gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{64}\Z")
PNGS = re.compile(r"observation-[0-9]{3}\.png\Z")
MAX_MISSION = 16 * 1024
MAX_LOG = 1024 * 1024
MAX_TRACE = 32 * 1024 * 1024
PNG_SIG = b"\x89PNG\r\n\x1a\n"


class AuditBlocked(Exception):
    pass


def require(cond: bool, reason: str) -> None:
    if not cond:
        raise AuditBlocked(reason)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def bounded(path: Path, maximum: int) -> bytes:
    require(path.is_file() and not path.is_symlink(), "MISSING_OR_SYMLINK_EVIDENCE")
    size = path.stat().st_size
    require(0 < size <= maximum, "OVERSIZED_EVIDENCE")
    return path.read_bytes()


def obj(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuditBlocked("INVALID_JSON") from exc
    require(type(data) is dict, "INVALID_DOCUMENT")
    return data


def assess(mission_raw: bytes, expected_mission_hash: str, expected_html_hash: str,
           output: Path) -> dict:
    require(type(expected_mission_hash) is str and bool(SHA.fullmatch(expected_mission_hash))
            and digest(mission_raw) == expected_mission_hash, "UNPINNED_MISSION")
    require(type(expected_html_hash) is str and bool(SHA.fullmatch(expected_html_hash)),
            "UNPINNED_RUNTIME")
    require(output.is_dir() and not output.is_symlink(), "MISSING_RUN_DIRECTORY")
    mission = obj(mission_raw)
    require(mission.get("schema_version") == 1
            and mission.get("kind") == "independent-ai-browser-mission",
            "INVALID_MISSION")
    outcomes = mission.get("expected_outcomes")
    require(type(outcomes) is list and 1 <= len(outcomes) <= 10, "INVALID_ORACLES")
    oracle_ids = {x["id"] for x in outcomes if type(x) is dict and type(x.get("id")) is str}
    require(len(oracle_ids) == len(outcomes), "INVALID_ORACLES")

    raw_report = bounded(output / "final.json", MAX_LOG)
    report = obj(raw_report)
    require(report.get("kind") == "independent-ai-autonomous-exploration"
            and report.get("authority") == "T0_ADVISORY_ONLY"
            and report.get("actual_model_invocation_verified") is False
            and report.get("human_equivalent_release_gate") is False
            and report.get("release_authorized") is False
            and report.get("product_full_user_e2e_verified") is False, "FORGED_AUTHORITY")
    require(report.get("mission_sha256") == expected_mission_hash
            and report.get("runtime_html_sha256") == expected_html_hash, "STALE_OR_ALTERED_IDENTITY")
    require(type(report.get("browser_version")) is str
            and bool(re.match(r"^\d+\.\d+", report["browser_version"])),
            "MISSING_REAL_BROWSER_METADATA")
    require(report.get("model_disposition") in {
        "NO_CONFIRMED_FINDING", "POTENTIAL_FINDING", "BLOCKED", "UNVERIFIED"
    }, "INVALID_DISPOSITION")
    require(type(report.get("denied_network_requests")) is list
            and type(report.get("browser_errors")) is list
            and type(report.get("claimed_findings")) is list, "MALFORMED_REPORT")
    if report["model_disposition"] == "NO_CONFIRMED_FINDING":
        require(not report["denied_network_requests"] and not report["browser_errors"]
                and not report["claimed_findings"], "FALSE_CLEAR_RESULT")
    statuses = report.get("oracle_status")
    require(type(statuses) is dict and set(statuses) == oracle_ids,
            "MISSING_ORACLE_STATUS")
    require(all(v in {"OBSERVED", "FAIL", "UNVERIFIED", "BLOCKED"} for v in statuses.values()),
            "INVALID_ORACLE_STATUS")

    raw_events = bounded(output / "events.jsonl", MAX_LOG)
    try:
        records = [obj(line) for line in raw_events.splitlines()]
    except AuditBlocked:
        raise
    require(len(records) <= 100 and len(records) >= 4, "INVALID_EVENT_COUNT")
    events = [x for x in records if x.get("kind") == "observation"]
    acts = [x for x in records if x.get("kind") == "ai_selected_action"]
    finished = [x for x in records if x.get("kind") == "session_finished"]
    require(len(finished) == 1 and records[-1].get("kind") == "session_finished",
            "MISSING_TERMINAL_RECORD")
    require(report.get("observations") == len(events) and report.get("actual_model_selected_actions") == len(acts)
            and len(acts) <= mission.get("max_actions", 0) and len(acts) >= 1
            and len(events) >= len(acts) + 1, "ACTION_OR_OBSERVATION_COUNT_MISMATCH")
    for i, step in enumerate(events, start=1):
        data = step.get("observation")
        require(type(data) is dict and data.get("ordinal") == i
                and type(step.get("id")) is str and SHA.fullmatch(step["id"]),
                "MISORDERED_OBSERVATION")
        image = data.get("screenshot")
        require(type(image) is dict and PNGS.fullmatch(str(image.get("name", "")))
                and type(image.get("sha256")) is str and SHA.fullmatch(image["sha256"]),
                "MISSING_SCREENSHOT_DIGEST")
        raw_img = bounded(output / image["name"], 6 * 1024 * 1024)
        require(raw_img.startswith(PNG_SIG) and digest(raw_img) == image["sha256"],
                "SCREENSHOT_CHANGED")
        if i == 1:
            require(data.get("after") == "initial-navigation" and data.get("actions_used") == 0,
                    "INVALID_INITIAL_OBSERVATION")
    ordered = [x for x in records if x.get("kind") in {"observation", "ai_selected_action"}]
    require(len(ordered) == len(events) + len(acts), "MISSING_CAUSAL_RECORD")
    for i, step in enumerate(ordered):
        if i % 2 == 0:
            require(step.get("kind") == "observation", "BROKEN_OBSERVATION_SEQUENCE")
        else:
            require(step.get("kind") == "ai_selected_action"
                    and step.get("number") == (i + 1) // 2
                    and step.get("observation_id") == ordered[i - 1].get("id")
                    and type(step.get("model_reason")) is str
                    and len(step["model_reason"]) >= 10, "UNVERIFIED_MODEL_CHOICE")
            require(step.get("action_error") is None or
                    report["model_disposition"] != "NO_CONFIRMED_FINDING",
                    "IGNORED_ACTION_ERROR")
    require(len(ordered) % 2 == 1, "MISSING_POST_ACTION_OBSERVATION")
    trace = bounded(output / "chromium-trace.zip", MAX_TRACE)
    require(trace[:2] == b"PK"
            and digest(trace) == report.get("trace_sha256"), "TRACE_CHANGED")
    if report["model_disposition"] == "NO_CONFIRMED_FINDING":
        # A networked analysis mission cannot claim all four outcomes on a mere landing screen.
        post_paths = mission.get("allowed_post_paths") or []
        if post_paths:
            require(any(entry.get("method") == "POST" and entry.get("status") == 200
                        and entry.get("path") in post_paths
                        for evt in events for entry in evt["observation"].get("recent_http", [])),
                    "NO_REAL_ANALYSIS_OBSERVED")
    return {
        "kind": "ai-qa-exploration-evidence-audit",
        "assessment": "EVIDENCE_CONSISTENT_T0",
        "authority": "T0_ADVISORY_ONLY",
        "independent_llm_provider_verified": False,
        "release_authorized": False,
        "full_user_e2e_verified": False,
        "plan_sha256": expected_mission_hash,
        "runtime_html_sha256": expected_html_hash,
        "actions": len(acts),
        "observations": len(events),
        "model_disposition": report["model_disposition"],
        "trace_sha256": digest(trace),
        "final_report_sha256": digest(raw_report),
        "events_sha256": digest(raw_events),
        "unverified_oracles": sorted(k for k, v in statuses.items() if v in {"UNVERIFIED", "BLOCKED"}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission", type=Path, required=True)
    parser.add_argument("--expected-mission-sha256", required=True)
    parser.add_argument("--expected-html-sha256", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = assess(bounded(args.mission, MAX_MISSION),
                        args.expected_mission_sha256,
                        args.expected_html_sha256, args.run_dir)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (AuditBlocked, OSError, TypeError, KeyError) as exc:
        reason = str(exc) if isinstance(exc, AuditBlocked) else "MALFORMED_OR_INACCESSIBLE"
        print(json.dumps({"kind": "ai-qa-exploration-evidence-audit",
                          "assessment": "BLOCK",
                          "reason": reason,
                          "authority": "T0_ADVISORY_ONLY",
                          "release_authorized": False}, sort_keys=True))
        return 3


if __name__ == "__main__":
    sys.exit(main())
