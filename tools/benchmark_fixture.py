#!/usr/bin/env python3
"""Validate the frozen historical benchmark fixture manifest.

The checker is local and network-free. It accepts immutable commit identities,
a worker-visible task contract, predeclared oracles, and the #44 result-field
list. FIXTURE_ID is <manifest git sha>:<case id>. The caller supplies the
40-hex commit that contains this manifest; a task, oracle, or source edit
is a different commit, so an older id does not bind. The checker rejects
mutable refs, missing oracles, secret-bearing metadata,
production-mutation instructions, and any aggregate score. It does not run
control or candidate benchmarks.
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

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "evals" / "benchmark" / "fixtures.yaml"
SCHEMA_PATH = ROOT / "schemas" / "benchmark-fixture.schema.json"

CONTROL_VERSION = "1.6.5"
CONTROL_HEAD = "14150e424c922ff3a930b45dcf31d3a3d3ba28b2"
CANDIDATE_HEAD = "cdee66ebebdddcdf3036562d5dbd3a1ebca9d921"
CANDIDATE_TREE = "a96ee616095ae5b07623ac9ea1ad7ddd3542ba20"
REQUIRED_CASE_IDS = (
    "BENCH-BUG-001",
    "BENCH-CROSS-002",
    "BENCH-INCIDENT-003",
    "BENCH-CLI-004",
    "BENCH-UI-005",
    "BENCH-DOCS-006",
    "BENCH-MULTI-007",
)
RESULT_FIELDS = (
    "CASE_ID",
    "SYSTEM_VERSION",
    "SYSTEM_HEAD",
    "FIXTURE_ID",
    "TERMINAL",
    "CORRECT_BEHAVIOR",
    "SAFETY_REGRESSION",
    "REGRESSION_TESTS",
    "EXACT_HEAD_EVIDENCE",
    "RELEVANT_CONTEXT",
    "WALL_SECONDS",
    "MODEL_COST",
    "RETRIES",
    "REREADS",
    "COMPACTIONS",
    "REVIEW_REWORK",
    "HUMAN_INTERVENTIONS",
    "HOST_RESOURCE_OUTCOME",
    "NOTES_CODE",
)
CORE_EVIDENCE = (
    "CORRECT_BEHAVIOR",
    "SAFETY_REGRESSION",
    "REGRESSION_TESTS",
    "EXACT_HEAD_EVIDENCE",
    "RELEVANT_CONTEXT",
)
PROHIBITED_KEYS = frozenset(
    {
        "prompt",
        "prompts",
        "conversation",
        "transcript",
        "secret",
        "secrets",
        "token",
        "tokens",
        "password",
        "passwords",
        "credential",
        "credentials",
        "private_key",
        "api_key",
        "stdout",
        "stderr",
        "log",
        "logs",
        "score",
        "weighted_score",
        "aggregate_score",
        "branch",
        "tag",
        "ref",
    }
)
MUTABLE_REF_VALUES = frozenset(
    {
        "HEAD",
        "head",
        "main",
        "master",
        "main-v2",
        "origin/main",
        "origin/master",
        "origin/main-v2",
    }
)
SECRET_VALUE_RE = re.compile(
    r"(-----BEGIN|ghp_|github_pat_|sk-|AKIA[0-9A-Z]{16}|xox[baprs]-)",
)
MUTABLE_REF_RE = re.compile(r"refs/(heads|tags)/")
PRODUCTION_MUTATION_RE = re.compile(
    r"(mutate production|production mutation|deploy to production|push --force|force-push)",
    re.IGNORECASE,
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CASE_ID_RE = re.compile(r"^BENCH-[A-Z0-9-]+$")
FIXTURE_ID_RE = re.compile(r"^([0-9a-f]{40}):(BENCH-[A-Z0-9-]+)$")
SNAPSHOT_AT = "2026-09-23T03:29:00Z"
CONCLUSION_RE = re.compile(r"root cause", re.IGNORECASE)
INCIDENT_EVIDENCE_CODES = (
    "HOST_CAPACITY_20GIB_RAM_6CPU_4GIB_SWAP",
    "SWAP_FULL_0610_THROUGH_1040",
    "RAM_USED_ABOUT_91_PERCENT",
    "COMMIT_CHARGE_ABOVE_150_PERCENT",
    "LOAD_AND_BLOCKED_TASKS_ESCALATED",
    "IO_WAIT_74_TO_85_PERCENT",
    "PTY_CRASH_RECORDS_67",
    "NO_KERNEL_OOM_KILLER_IN_PREVIOUS_BOOT",
    "POST_REBOOT_MEMORY_NORMALIZED",
    "DELIVERY_LOGS_TREE_ABOUT_49_GIB",
    "HISTORICAL_COUNT_QUERY_ABOUT_40_SECONDS",
    "DEV_VALIDATION_MAX_DELETED_ERRORS",
    "STREAMS_POLLED_EVERY_SECOND",
)
WORKER_TASK_KEYS = (
    "case_id",
    "repository",
    "source_commit",
    "objective",
    "objective_codes",
    "reset",
    "excludes_secrets",
    "excludes_production_mutation",
)


class FixtureError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _reject_prohibited(value: Any) -> None:
    if isinstance(value, dict):
        found = PROHIBITED_KEYS.intersection(value)
        if found:
            raise FixtureError("PROHIBITED_FIXTURE_FIELD")
        for item in value.values():
            _reject_prohibited(item)
    elif isinstance(value, list):
        for item in value:
            _reject_prohibited(item)


def _scan_strings(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _scan_strings(item)
        return
    if isinstance(value, list):
        for item in value:
            _scan_strings(item)
        return
    if not isinstance(value, str):
        return
    if value in MUTABLE_REF_VALUES or MUTABLE_REF_RE.search(value):
        raise FixtureError("MUTABLE_REF")
    if SECRET_VALUE_RE.search(value):
        raise FixtureError("SECRET_BEARING_FIXTURE")
    if PRODUCTION_MUTATION_RE.search(value):
        raise FixtureError("PRODUCTION_MUTATION_INSTRUCTION")


def _schema_error(instance: Any) -> None:
    validator = Draft202012Validator(_load_json(SCHEMA_PATH))
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.path))
    if errors:
        raise FixtureError("SCHEMA_INVALID")


def validate_manifest(instance: Any) -> dict[str, Any]:
    if not isinstance(instance, dict):
        raise FixtureError("SCHEMA_INVALID")
    _reject_prohibited(instance)
    _scan_strings(instance)
    _schema_error(instance)
    control = instance["control"]
    candidate = instance["candidate"]
    if control["version"] != CONTROL_VERSION or control["head"] != CONTROL_HEAD:
        raise FixtureError("CONTROL_ANCHOR_MISMATCH")
    if candidate["head"] != CANDIDATE_HEAD or candidate["tree"] != CANDIDATE_TREE:
        raise FixtureError("CANDIDATE_ANCHOR_MISMATCH")
    if tuple(instance["result_fields"]) != RESULT_FIELDS:
        raise FixtureError("RESULT_FIELDS_MISMATCH")
    cases = instance["cases"]
    ids = tuple(case["id"] for case in cases)
    if ids != REQUIRED_CASE_IDS:
        raise FixtureError("CASE_SET_MISMATCH")
    if len(set(ids)) != len(ids):
        raise FixtureError("DUPLICATE_CASE")
    for case in cases:
        if case["status"] != "FROZEN":
            continue
        missing = [name for name in CORE_EVIDENCE if name not in case["evidence"]]
        if missing:
            raise FixtureError("MISSING_EVIDENCE")
        if not case["oracle"]:
            raise FixtureError("MISSING_ORACLE")
        commits = [case["source_commit"], *case["lineage_commits"]]
        if len(commits) != len(set(commits)):
            raise FixtureError("DUPLICATE_COMMIT")
        if _record_repository(case["source_record"]) != case["repository"]:
            raise FixtureError("SOURCE_RECORD_MISMATCH")
        _reject_task_oracle_leak(case)
        _require_historical_inputs(case)
    return instance


def _record_repository(source_record: str) -> str:
    repository, marker, number = source_record.rpartition("#")
    if marker != "#" or not number.isdecimal() or not repository:
        raise FixtureError("SOURCE_RECORD_MISMATCH")
    return repository


def _visible_worker_text(case: dict[str, Any]) -> list[str]:
    task = case["task"]
    chunks = [task["objective"], *task["objective_codes"]]
    for item in case.get("evidence_input", []):
        chunks.extend((item["code"], item["fact"]))
    return chunks


def _reject_task_oracle_leak(case: dict[str, Any]) -> None:
    task = case["task"]
    oracle = set(case["oracle"])
    if oracle.intersection(task["objective_codes"]):
        raise FixtureError("TASK_ORACLE_OVERLAP")
    for chunk in _visible_worker_text(case):
        if any(code in chunk for code in oracle):
            raise FixtureError("TASK_LEAKS_ORACLE")
        if CONCLUSION_RE.search(chunk):
            raise FixtureError("EVIDENCE_LEAKS_CONCLUSION")


def _require_historical_inputs(case: dict[str, Any]) -> None:
    if case["id"] == "BENCH-INCIDENT-003":
        codes = tuple(item["code"] for item in case.get("evidence_input", []))
        if codes != INCIDENT_EVIDENCE_CODES:
            raise FixtureError("EVIDENCE_INPUT_MISMATCH")
    elif "evidence_input" in case:
        raise FixtureError("UNEXPECTED_EVIDENCE_INPUT")
    if case["id"] != "BENCH-MULTI-007":
        if "rollout_topology" in case or "snapshot_at" in case:
            raise FixtureError("UNEXPECTED_ROLLOUT_TOPOLOGY")
        return
    if case.get("snapshot_at") != SNAPSHOT_AT:
        raise FixtureError("SNAPSHOT_MISMATCH")
    topology = case.get("rollout_topology")
    if not topology or len(topology) != 14:
        raise FixtureError("MISSING_ROLLOUT_TOPOLOGY")
    canonical = [item for item in topology if item["role"] == "CANONICAL"]
    if len(canonical) != 1 or canonical[0]["repository"] != case["repository"]:
        raise FixtureError("ROLLOUT_TOPOLOGY_MISMATCH")
    seen = []
    for item in topology:
        if item["snapshot_state"] != "OPEN":
            raise FixtureError("SNAPSHOT_STATE_MISMATCH")
        if _record_repository(item["source_record"]) != item["repository"]:
            raise FixtureError("SOURCE_RECORD_MISMATCH")
        if not item["source_record"].endswith("#" + str(item["pull_request"])):
            raise FixtureError("SOURCE_RECORD_MISMATCH")
        seen.append((item["repository"], item["pull_request"]))
    if len(seen) != len(set(seen)):
        raise FixtureError("ROLLOUT_TOPOLOGY_MISMATCH")


def fixture_id(manifest_git_sha: str, case_id: str) -> str:
    """Return the #44 FIXTURE_ID for one manifest commit and case."""
    if SHA_RE.fullmatch(manifest_git_sha) is None or CASE_ID_RE.fullmatch(case_id) is None:
        raise FixtureError("FIXTURE_ID_INVALID")
    return f"{manifest_git_sha}:{case_id}"


def _case_by_id(manifest: dict[str, Any], case_id: str) -> dict[str, Any]:
    matches = [case for case in manifest["cases"] if case["id"] == case_id]
    if len(matches) != 1:
        raise FixtureError("FIXTURE_ID_INVALID")
    return matches[0]


def bind_fixture_id(value: str, manifest: dict[str, Any], manifest_git_sha: str) -> dict[str, str]:
    """Bind a result FIXTURE_ID to the supplied manifest commit and case."""
    if not isinstance(manifest, dict) or SHA_RE.fullmatch(manifest_git_sha) is None:
        raise FixtureError("FIXTURE_ID_INVALID")
    match = FIXTURE_ID_RE.fullmatch(value)
    if match is None:
        raise FixtureError("FIXTURE_ID_INVALID")
    bound_sha, case_id = match.groups()
    if bound_sha != manifest_git_sha:
        raise FixtureError("FIXTURE_REVISION_MISMATCH")
    case = _case_by_id(manifest, case_id)
    if case.get("status") != "FROZEN":
        raise FixtureError("CASE_NOT_RUNNABLE")
    return {
        "manifest_git_sha": manifest_git_sha,
        "case_id": case_id,
        "fixture_id": value,
    }


def worker_task(manifest: dict[str, Any], case_id: str) -> dict[str, Any]:
    """Return the sanitized task a Phase-B worker may see.

    The projection omits the oracle, outcome lineage, mutable issue record, and FIXTURE_ID.
    Incident evidence and the multi-repo rollout topology are included when frozen.
    """
    if not isinstance(manifest, dict):
        raise FixtureError("CASE_NOT_RUNNABLE")
    case = _case_by_id(manifest, case_id)
    if case.get("status") != "FROZEN":
        raise FixtureError("CASE_NOT_RUNNABLE")
    _reject_task_oracle_leak(case)
    task = case["task"]
    projected = {
        "case_id": case["id"],
        "repository": case["repository"],
        "source_commit": case["source_commit"],
        "objective": task["objective"],
        "objective_codes": list(task["objective_codes"]),
        "reset": case["reset"],
        "excludes_secrets": case["excludes_secrets"],
        "excludes_production_mutation": case["excludes_production_mutation"],
    }
    allowed = set(WORKER_TASK_KEYS)
    if "evidence_input" in case:
        projected["evidence_input"] = [
            {"code": item["code"], "fact": item["fact"]} for item in case["evidence_input"]
        ]
        allowed.add("evidence_input")
    if "rollout_topology" in case:
        projected["snapshot_at"] = case["snapshot_at"]
        projected["rollout_topology"] = [
            {
                "repository": item["repository"],
                "pull_request": item["pull_request"],
                "role": item["role"],
                "snapshot_head_commit": item["snapshot_head_commit"],
                "snapshot_state": item["snapshot_state"],
            }
            for item in case["rollout_topology"]
        ]
        allowed.add("snapshot_at")
        allowed.add("rollout_topology")
    if set(projected) != allowed:
        raise FixtureError("TASK_LEAKS_ORACLE")
    return projected


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return validate_manifest(loaded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    args = parser.parse_args(argv)
    if args.command != "validate":
        print("FAIL UNSUPPORTED_COMMAND")
        return 1
    try:
        load_manifest(args.manifest)
    except FixtureError as exc:
        print(f"FAIL {exc.code}")
        return 1
    print("PASS benchmark fixture manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
