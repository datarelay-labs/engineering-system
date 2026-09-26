#!/usr/bin/env python3
"""Prepare a frozen-case control/candidate dry-run without launching workers.

The coordinator may read the frozen manifest. A worker payload is only
``worker_task()`` plus explicit run metadata. The dry-run binds one of the
seven frozen case identities and emits a non-final result template. Observed
cost, time, and counts are derived from a canonical efficiency telemetry
record; this module does not emit a second telemetry record. It does not
start a model, agent, or network call.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
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
CANDIDATE_SYSTEM_VERSION = "1.7"
# Repository and source commit for each frozen case at PILOT_MANIFEST_HEAD.
# These stay explicit so a mutated manifest fails before blob equality, and
# the BENCH-BUG-001 source check remains TASK_SOURCE_MISMATCH.
FROZEN_CASE_IDENTITIES = {
    "BENCH-BUG-001": {
        "repository": "datarelay-labs/engineering-system",
        "source_commit": "3cdedad5a40105aea426abda0df8d7c258e5e8ad",
    },
    "BENCH-CROSS-002": {
        "repository": "datarelay-labs/datarelay-link",
        "source_commit": "3088e0067dbada08267eeaae100ca2f42b3b0758",
    },
    "BENCH-INCIDENT-003": {
        "repository": "datarelay-labs/datarelay-control",
        "source_commit": "2412616607d405b22e311c963a0f40e8c0daba27",
    },
    "BENCH-CLI-004": {
        "repository": "datarelay-labs/datarelay-link",
        "source_commit": "3f931c2708c867062224acf4eedd1aef48d3028f",
    },
    "BENCH-UI-005": {
        "repository": "datarelay-labs/datarelay-control",
        "source_commit": "322b061c2640eb03f2c4b5bb29bb62e7c81a6421",
    },
    "BENCH-DOCS-006": {
        "repository": "datarelay-labs/datarelay-link",
        "source_commit": "066513fe7cc3b6416b5763f82d4c1c68a4e15ecc",
    },
    "BENCH-MULTI-007": {
        "repository": "datarelay-labs/engineering-system",
        "source_commit": "f3a6856a81c7bee307ca8a6cf256faa73514610e",
    },
}
PILOT_REPOSITORY = FROZEN_CASE_IDENTITIES[PILOT_CASE_ID]["repository"]
PILOT_TASK_SOURCE_HEAD = FROZEN_CASE_IDENTITIES[PILOT_CASE_ID]["source_commit"]
LANE_WORKSTREAM = {
    "CONTROL": f"{PILOT_CASE_ID.lower()}-control",
    "CANDIDATE": f"{PILOT_CASE_ID.lower()}-candidate",
}
NAME_RE = re.compile(r"^[A-Za-z0-9_.:@\[\]=,+-]{1,80}$")
TELEMETRY_DERIVATIONS = (
    {"result_field": "WALL_SECONDS", "source": "duration_seconds", "when_null": "UNKNOWN"},
    {"result_field": "MODEL_COST", "source": "usage.cost", "when_null": "UNKNOWN"},
    {"result_field": "RETRIES", "source": "counts.retries", "when_null": "UNAVAILABLE"},
    {"result_field": "REREADS", "source": "counts.rereads", "when_null": "UNAVAILABLE"},
    {"result_field": "COMPACTIONS", "source": "counts.compactions", "when_null": "UNAVAILABLE"},
    {
        "result_field": "REVIEW_REWORK",
        "source": "rework_count",
        "aggregate": "efficiency_telemetry.rework_count",
    },
    {"result_field": "HUMAN_INTERVENTIONS", "source": "counts.human_interventions", "when_null": "UNAVAILABLE"},
    {
        "result_field": "EXACT_HEAD_EVIDENCE",
        "source": "validation.evidence_state",
        "value_map": {"EXACT_HEAD": "PASS", "STALE": "FAIL", "MISSING": "MISSING"},
    },
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


def lane_workstream(case_id: str, lane: str) -> str:
    """Return the telemetry workstream for one frozen case and lane."""
    if lane not in LANE_WORKSTREAM:
        raise ExecutionError("LANE_INVALID")
    if case_id not in FROZEN_CASE_IDENTITIES:
        raise ExecutionError("CASE_NOT_IN_PILOT")
    return f"{case_id.lower()}-{lane.lower()}"


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
    lane_heads = {benchmark_fixture.CONTROL_HEAD, benchmark_fixture.CANDIDATE_HEAD}

    def walk(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            if FORBIDDEN_WORKER_KEYS.intersection(value):
                raise ExecutionError("TASK_LEAKS_ORACLE")
            for child_key, item in value.items():
                walk(item, child_key)
            return
        if isinstance(value, list):
            for item in value:
                walk(item, key)
            return
        if isinstance(value, str) and value in banned_values:
            # Lane system heads are explicit run metadata. BENCH-MULTI-007's
            # lineage also names the control baseline, which is not a task leak.
            if key == "system_head" and value in lane_heads:
                return
            raise ExecutionError("TASK_LEAKS_ORACLE")
        if isinstance(value, str) and benchmark_fixture.PRODUCTION_MUTATION_RE.search(value):
            raise ExecutionError("PRODUCTION_MUTATION_INSTRUCTION")

    walk(payload)


def _lookup(record: dict[str, Any], source: str) -> Any:
    value: Any = record
    for part in source.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ExecutionError("TELEMETRY_DERIVATION_INVALID")
        value = value[part]
    return value


def telemetry_mapping() -> dict[str, Any]:
    """Reference canonical telemetry fields. This is not a telemetry record."""
    return {
        "authority": "tools/efficiency_telemetry.py",
        "schema": "schemas/efficiency-telemetry.schema.json",
        "derivations": [dict(item) for item in TELEMETRY_DERIVATIONS],
    }


def _require_frozen_manifest(manifest: dict[str, Any]) -> None:
    """Reject content that is not the blob at the frozen manifest revision."""
    relative = benchmark_fixture.MANIFEST_PATH.relative_to(ROOT).as_posix()
    try:
        text = efficiency_telemetry._git_output(ROOT, "show", f"{PILOT_MANIFEST_HEAD}:{relative}")
    except efficiency_telemetry.TelemetryError as exc:
        raise ExecutionError("FROZEN_MANIFEST_UNAVAILABLE") from exc
    if yaml.safe_load(text) != manifest:
        raise ExecutionError("FROZEN_MANIFEST_MISMATCH")


def _bind_telemetry_lane(
    parsed: dict[str, Any],
    *,
    case_id: str,
    lane: str,
    system_head: str,
    profile: dict[str, Any],
    run_id: str,
) -> None:
    if case_id not in FROZEN_CASE_IDENTITIES:
        raise ExecutionError("CASE_NOT_IN_PILOT")
    if lane not in LANE_WORKSTREAM:
        raise ExecutionError("LANE_INVALID")
    expected_head = benchmark_fixture.CONTROL_HEAD if lane == "CONTROL" else benchmark_fixture.CANDIDATE_HEAD
    repository = FROZEN_CASE_IDENTITIES[case_id]["repository"]
    if system_head != expected_head or parsed["repo"] != repository:
        raise ExecutionError("TELEMETRY_LANE_MISMATCH")
    if parsed["workstream"] != lane_workstream(case_id, lane) or parsed["run_id"] != run_id:
        raise ExecutionError("TELEMETRY_LANE_MISMATCH")
    try:
        actual = efficiency_telemetry.normalize_profile(parsed["profile"])
        expected = efficiency_telemetry.normalize_profile(profile)
    except efficiency_telemetry.TelemetryError as exc:
        raise ExecutionError(exc.code) from exc
    if actual != expected:
        raise ExecutionError("TELEMETRY_LANE_MISMATCH")
    validation = parsed["validation"]
    exact_head = validation["exact_head"]
    if validation["evidence_state"] == "EXACT_HEAD":
        if exact_head != system_head:
            raise ExecutionError("TELEMETRY_LANE_MISMATCH")
    elif exact_head not in (None, system_head):
        raise ExecutionError("TELEMETRY_LANE_MISMATCH")


def derive_observed_fields(
    record: dict[str, Any],
    *,
    lane: str,
    system_head: str,
    profile: dict[str, Any],
    run_id: str,
    case_id: str = PILOT_CASE_ID,
) -> dict[str, Any]:
    """Map one canonical telemetry record into #44 fields for one benchmark lane."""
    try:
        parsed = efficiency_telemetry.parse_document(record)
    except efficiency_telemetry.TelemetryError as exc:
        raise ExecutionError(exc.code) from exc
    if parsed.get("kind") != "efficiency-telemetry":
        raise ExecutionError("TELEMETRY_RECORD_REQUIRED")
    _bind_telemetry_lane(
        parsed,
        case_id=case_id,
        lane=lane,
        system_head=system_head,
        profile=profile,
        run_id=run_id,
    )
    derived: dict[str, Any] = {}
    for rule in TELEMETRY_DERIVATIONS:
        if "aggregate" in rule:
            if (
                rule["aggregate"] != "efficiency_telemetry.rework_count"
                or rule["result_field"] != "REVIEW_REWORK"
                or rule["source"] != "rework_count"
            ):
                raise ExecutionError("TELEMETRY_DERIVATION_INVALID")
            derived[rule["result_field"]] = efficiency_telemetry.rework_count(parsed["counts"])
            continue
        value = _lookup(parsed, rule["source"])
        if "value_map" in rule:
            mapped = rule["value_map"].get(value)
            if mapped is None:
                raise ExecutionError("TELEMETRY_DERIVATION_INVALID")
            derived[rule["result_field"]] = mapped
            continue
        derived[rule["result_field"]] = rule["when_null"] if value is None else value
    return derived


def accepts_final_result(instance: Any) -> bool:
    """Return whether a document is a final #44 result. Templates are not."""
    schema = _schema()["$defs"]["final_result"]
    return not any(Draft202012Validator(schema).iter_errors(instance))


def bind_final_result(instance: Any, *, case_id: str, lane: str) -> bool:
    """Return whether a final result is the selected frozen case and lane."""
    if not isinstance(instance, dict):
        return False
    if case_id not in FROZEN_CASE_IDENTITIES or lane not in LANE_WORKSTREAM:
        return False
    if not accepts_final_result(instance):
        return False
    if lane == "CONTROL":
        expected_version = benchmark_fixture.CONTROL_VERSION
        expected_head = benchmark_fixture.CONTROL_HEAD
    else:
        expected_version = CANDIDATE_SYSTEM_VERSION
        expected_head = benchmark_fixture.CANDIDATE_HEAD
    return (
        instance.get("CASE_ID") == case_id
        and instance.get("FIXTURE_ID") == f"{PILOT_MANIFEST_HEAD}:{case_id}"
        and instance.get("SYSTEM_VERSION") == expected_version
        and instance.get("SYSTEM_HEAD") == expected_head
    )


def _result_template(case_id: str, system_version: str, system_head: str, fixture_id: str) -> dict[str, Any]:
    known = {
        "CASE_ID": case_id,
        "SYSTEM_VERSION": system_version,
        "SYSTEM_HEAD": system_head,
        "FIXTURE_ID": fixture_id,
        "WALL_SECONDS": "UNKNOWN",
        "MODEL_COST": "UNKNOWN",
    }
    fields = {name: known.get(name) for name in benchmark_fixture.RESULT_FIELDS}
    if tuple(fields) != benchmark_fixture.RESULT_FIELDS:
        raise ExecutionError("RESULT_FIELDS_MISMATCH")
    template = {"kind": "benchmark-result-template", "final": False, "fields": fields}
    if accepts_final_result(template) or accepts_final_result(fields):
        raise ExecutionError("TEMPLATE_ACCEPTED_AS_FINAL")
    return template


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
        "result_template": _result_template(case["id"], system_version, system_head, fixture_id),
    }


def _envelope(
    *,
    case_id: str,
    manifest_git_sha: str,
    comparison: str,
    reason: str,
    lanes: list[dict[str, Any]],
) -> dict[str, Any]:
    document = {
        "schema_version": 1,
        "kind": "benchmark-execution-dry-run",
        "case_id": case_id,
        "manifest_git_sha": manifest_git_sha,
        "fixture_id": f"{manifest_git_sha}:{case_id}",
        "comparison": comparison,
        "reason": reason,
        "execute_worker": False,
        "network": "NONE",
        "telemetry_mapping": telemetry_mapping(),
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
    """Return one frozen-case dry-run plan, or a fail-closed profile comparison."""
    if tuple(FROZEN_CASE_IDENTITIES) != benchmark_fixture.REQUIRED_CASE_IDS:
        raise ExecutionError("CASE_SET_MISMATCH")
    if case_id not in FROZEN_CASE_IDENTITIES:
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
    identity = FROZEN_CASE_IDENTITIES[case_id]
    if case["source_commit"] != identity["source_commit"]:
        raise ExecutionError("TASK_SOURCE_MISMATCH")
    if case["repository"] != identity["repository"]:
        raise ExecutionError("REPOSITORY_MISMATCH")
    _require_frozen_manifest(validated)
    control_profile = _complete_profile(profile)
    other = profile if candidate_profile is None else candidate_profile
    candidate_profile_norm = _complete_profile(other)
    fixture_binding = benchmark_fixture.bind_fixture_id(
        f"{manifest_git_sha}:{case_id}",
        validated,
        manifest_git_sha,
    )
    if fixture_binding["fixture_id"] != f"{PILOT_MANIFEST_HEAD}:{case_id}":
        raise ExecutionError("FIXTURE_REVISION_MISMATCH")
    if control_profile is None or candidate_profile_norm is None:
        return _envelope(
            case_id=case_id,
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
        case_id=case_id,
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
    dry.add_argument("--case-id", default=PILOT_CASE_ID)
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
            case_id=args.case_id,
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
