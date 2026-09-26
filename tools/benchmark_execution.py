#!/usr/bin/env python3
"""Prepare a BENCH-BUG-001 control/candidate dry-run without launching workers.

The coordinator may read the frozen manifest. A worker payload is only
``worker_task()`` plus explicit run metadata. The dry-run binds the pilot
identities, reuses efficiency-telemetry profile and usage semantics, and
emits a #44 result skeleton. It does not start a model, agent, or network
call, and it does not write a second telemetry store.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import benchmark_fixture
import efficiency_telemetry

ROOT = TOOLS.parent
SCHEMA_PATH = ROOT / "schemas" / "benchmark-execution.schema.json"
PILOT_CASE_ID = "BENCH-BUG-001"
PILOT_MANIFEST_HEAD = "2990e6683f87a9858c4441e719ebf78b643c7ded"
PILOT_TASK_SOURCE_HEAD = "3cdedad5a40105aea426abda0df8d7c258e5e8ad"
CANDIDATE_SYSTEM_VERSION = "1.7"
NAME_RE = re.compile(r"^[A-Za-z0-9_.:@\[\]=,+-]{1,80}$")
SHARED_COUNT_FIELDS = (
    "retries",
    "rereads",
    "compactions",
    "review_rework",
    "human_interventions",
)
FORBIDDEN_WORKER_KEYS = frozenset(
    {
        "oracle",
        "lineage_commits",
        "source_record",
        "outcome",
        "merge_evidence",
        "conversation",
        "transcript",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "score",
        "weighted_score",
        "aggregate_score",
    }
)


class ExecutionError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _complete_profile(profile: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(profile, dict):
        return None
    try:
        normalized = efficiency_telemetry.normalize_profile(profile)
    except efficiency_telemetry.TelemetryError as exc:
        if exc.code != "PROFILE_INCOMPLETE":
            raise ExecutionError(exc.code) from exc
        return None
    if any(not isinstance(value, str) or NAME_RE.fullmatch(value) is None for value in normalized.values()):
        return None
    return normalized


def _reject_worker_leak(payload: dict[str, Any], case: dict[str, Any]) -> None:
    banned_values = set(case["oracle"])
    banned_values.update(case.get("lineage_commits") or [])
    banned_values.add(case["source_record"])

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if FORBIDDEN_WORKER_KEYS.intersection(value):
                raise ExecutionError("TASK_LEAKS_ORACLE")
            for item in value.values():
                walk(item)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if isinstance(value, str) and value in banned_values:
            raise ExecutionError("TASK_LEAKS_ORACLE")
        if isinstance(value, str) and benchmark_fixture.PRODUCTION_MUTATION_RE.search(value):
            raise ExecutionError("PRODUCTION_MUTATION_INSTRUCTION")

    walk(payload)


def _result(case_id: str, system_version: str, system_head: str, fixture_id: str) -> dict[str, Any]:
    result = {
        "CASE_ID": case_id,
        "SYSTEM_VERSION": system_version,
        "SYSTEM_HEAD": system_head,
        "FIXTURE_ID": fixture_id,
        "TERMINAL": "BLOCK",
        "CORRECT_BEHAVIOR": "UNKNOWN",
        "SAFETY_REGRESSION": "UNKNOWN",
        "REGRESSION_TESTS": "BLOCK",
        "EXACT_HEAD_EVIDENCE": "MISSING",
        "RELEVANT_CONTEXT": "MISSING",
        "WALL_SECONDS": "UNKNOWN",
        "MODEL_COST": "UNKNOWN",
        "RETRIES": 0,
        "REREADS": 0,
        "COMPACTIONS": 0,
        "REVIEW_REWORK": 0,
        "HUMAN_INTERVENTIONS": 0,
        "HOST_RESOURCE_OUTCOME": "UNKNOWN",
        "NOTES_CODE": "DRY_RUN_NOT_EXECUTED",
    }
    if tuple(result) != benchmark_fixture.RESULT_FIELDS:
        raise ExecutionError("RESULT_FIELDS_MISMATCH")
    return result


def _telemetry(profile: dict[str, str]) -> dict[str, Any]:
    return {
        "semantics": "efficiency-telemetry",
        "profile": dict(profile),
        "duration_seconds": None,
        "usage": efficiency_telemetry.usage_from_exposed(None),
        "counts": {field: 0 for field in SHARED_COUNT_FIELDS},
    }


def _lane(
    manifest: dict[str, Any],
    case: dict[str, Any],
    *,
    lane: str,
    manifest_git_sha: str,
    profile: dict[str, str],
) -> dict[str, Any]:
    if lane == "CONTROL":
        system_version = manifest["control"]["version"]
        system_head = manifest["control"]["head"]
    elif lane == "CANDIDATE":
        system_version = CANDIDATE_SYSTEM_VERSION
        system_head = manifest["candidate"]["head"]
    else:
        raise ExecutionError("LANE_INVALID")
    fixture_id = benchmark_fixture.fixture_id(manifest_git_sha, case["id"])
    benchmark_fixture.bind_fixture_id(fixture_id, manifest, manifest_git_sha)
    task = benchmark_fixture.worker_task(manifest, case["id"])
    payload = {
        "task": task,
        "run": {
            "lane": lane,
            "case_id": case["id"],
            "system_version": system_version,
            "system_head": system_head,
            "fixture_manifest_head": manifest_git_sha,
            "fixture_id": fixture_id,
            "task_source_head": case["source_commit"],
            "profile": dict(profile),
            "isolation": {
                "reset": case["reset"],
                "relative_directory": f"benchmark-dry-run/{case['id']}/{lane.lower()}",
                "execute_worker": False,
                "production_mutation": False,
            },
        },
    }
    _reject_worker_leak(payload, case)
    return {
        "lane": lane,
        "system_version": system_version,
        "system_head": system_head,
        "fixture_id": fixture_id,
        "task_source_head": case["source_commit"],
        "worker_payload": payload,
        "profile": dict(profile),
        "isolation": payload["run"]["isolation"],
        "result": _result(case["id"], system_version, system_head, fixture_id),
        "telemetry": _telemetry(profile),
    }


def _envelope(
    *,
    manifest_git_sha: str,
    comparison: str,
    reason: str,
    lanes: list[dict[str, Any]],
) -> dict[str, Any]:
    document = {
        "schema_version": 1,
        "kind": "benchmark-execution-dry-run",
        "case_id": PILOT_CASE_ID,
        "manifest_git_sha": manifest_git_sha,
        "fixture_id": f"{manifest_git_sha}:{PILOT_CASE_ID}",
        "comparison": comparison,
        "reason": reason,
        "execute_worker": False,
        "network": "NONE",
        "lanes": lanes,
    }
    errors = sorted(Draft202012Validator(_schema()).iter_errors(document), key=lambda item: list(item.path))
    if errors:
        raise ExecutionError("SCHEMA_INVALID")
    encoded = json.dumps(document)
    if "aggregate_score" in encoded or "weighted_score" in encoded:
        raise ExecutionError("AGGREGATE_SCORE")
    if benchmark_fixture.PRODUCTION_MUTATION_RE.search(encoded):
        raise ExecutionError("PRODUCTION_MUTATION_INSTRUCTION")
    return document


def dry_run(
    manifest: dict[str, Any],
    *,
    case_id: str,
    manifest_git_sha: str,
    profile: dict[str, Any] | None,
    candidate_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the pilot dry-run plan, or a fail-closed profile comparison."""
    if case_id != PILOT_CASE_ID:
        raise ExecutionError("CASE_NOT_IN_PILOT")
    if benchmark_fixture.SHA_RE.fullmatch(manifest_git_sha) is None:
        raise ExecutionError("FIXTURE_ID_INVALID")
    if manifest_git_sha != PILOT_MANIFEST_HEAD:
        raise ExecutionError("FIXTURE_REVISION_MISMATCH")
    if not isinstance(manifest, dict):
        raise ExecutionError("SCHEMA_INVALID")
    control = manifest.get("control")
    candidate = manifest.get("candidate")
    if (
        not isinstance(control, dict)
        or not isinstance(candidate, dict)
        or control.get("head") != benchmark_fixture.CONTROL_HEAD
        or candidate.get("head") != benchmark_fixture.CANDIDATE_HEAD
    ):
        raise ExecutionError("SYSTEM_HEAD_MISMATCH")
    try:
        validated = benchmark_fixture.validate_manifest(manifest)
    except benchmark_fixture.FixtureError as exc:
        raise ExecutionError(exc.code) from exc
    case = benchmark_fixture._case_by_id(validated, case_id)
    if case["source_commit"] != PILOT_TASK_SOURCE_HEAD:
        raise ExecutionError("TASK_SOURCE_MISMATCH")
    control_profile = _complete_profile(profile)
    other = profile if candidate_profile is None else candidate_profile
    candidate_profile_norm = _complete_profile(other)
    fixture_binding = benchmark_fixture.bind_fixture_id(
        f"{manifest_git_sha}:{case_id}",
        validated,
        manifest_git_sha,
    )
    if fixture_binding["fixture_id"] != f"{PILOT_MANIFEST_HEAD}:{PILOT_CASE_ID}":
        raise ExecutionError("FIXTURE_REVISION_MISMATCH")
    if control_profile is None or candidate_profile_norm is None:
        return _envelope(
            manifest_git_sha=manifest_git_sha,
            comparison="BLOCK",
            reason="PROFILE_INCOMPLETE",
            lanes=[],
        )
    if control_profile != candidate_profile_norm:
        comparison, reason = "PARTIAL", "PROFILE_MISMATCH"
    else:
        comparison, reason = "COMPARABLE", "PROFILE_MATCH"
    lanes = [
        _lane(
            validated,
            case,
            lane="CONTROL",
            manifest_git_sha=manifest_git_sha,
            profile=control_profile,
        ),
        _lane(
            validated,
            case,
            lane="CANDIDATE",
            manifest_git_sha=manifest_git_sha,
            profile=candidate_profile_norm,
        ),
    ]
    if lanes[0]["worker_payload"]["task"] != lanes[1]["worker_payload"]["task"]:
        raise ExecutionError("TASK_DIVERGED")
    if lanes[0]["system_head"] == lanes[1]["system_head"]:
        raise ExecutionError("SYSTEM_HEAD_MISMATCH")
    return _envelope(
        manifest_git_sha=manifest_git_sha,
        comparison=comparison,
        reason=reason,
        lanes=lanes,
    )


def _profile_from_args(args: argparse.Namespace, prefix: str) -> dict[str, Any] | None:
    values = {
        "provider": getattr(args, f"{prefix}provider"),
        "model": getattr(args, f"{prefix}model"),
        "reasoning": getattr(args, f"{prefix}reasoning"),
        "toolset": getattr(args, f"{prefix}toolset"),
    }
    if prefix == "candidate_" and all(value is None for value in values.values()):
        return None
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    dry = sub.add_parser("dry-run")
    dry.add_argument("--manifest", type=Path, default=benchmark_fixture.MANIFEST_PATH)
    dry.add_argument("--json", action="store_true")
    for prefix in ("", "candidate_"):
        for field in efficiency_telemetry.PROFILE_FIELDS:
            dry.add_argument(f"--{prefix.replace('_', '-')}{field}", default=None)
    args = parser.parse_args(argv)
    if args.command != "dry-run":
        print("FAIL UNSUPPORTED_COMMAND")
        return 1
    try:
        manifest = benchmark_fixture.load_manifest(args.manifest)
        document = dry_run(
            manifest,
            case_id=PILOT_CASE_ID,
            manifest_git_sha=PILOT_MANIFEST_HEAD,
            profile=_profile_from_args(args, ""),
            candidate_profile=_profile_from_args(args, "candidate_"),
        )
    except (ExecutionError, benchmark_fixture.FixtureError) as exc:
        print(f"FAIL {exc.code}")
        return 1
    if args.json:
        print(json.dumps(document, sort_keys=True))
    elif document["comparison"] == "COMPARABLE":
        print("PASS benchmark execution dry-run")
    else:
        print(f"{document['comparison']} {document['reason']}")
    return 0 if document["comparison"] == "COMPARABLE" else 1


if __name__ == "__main__":
    sys.exit(main())
