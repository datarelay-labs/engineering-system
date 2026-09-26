#!/usr/bin/env python3
"""Regressions for the BENCH-BUG-001 execution dry-run."""
from __future__ import annotations

import copy
import contextlib
import importlib.util
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "benchmark_execution.py"
PROFILE = {
    "provider": "example-provider",
    "model": "example-model",
    "reasoning": "low",
    "toolset": "read-only",
}


def _fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def load_tool():
    spec = importlib.util.spec_from_file_location("benchmark_execution", TOOL)
    if spec is None or spec.loader is None:
        raise SystemExit("FAIL cannot load benchmark_execution.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXEC = load_tool()


def _expect(code: str, **overrides: object) -> None:
    manifest = EXEC.benchmark_fixture.load_manifest()
    kwargs = {
        "case_id": EXEC.PILOT_CASE_ID,
        "manifest_git_sha": EXEC.PILOT_MANIFEST_HEAD,
        "profile": dict(PROFILE),
    }
    kwargs.update(overrides)
    try:
        EXEC.dry_run(manifest, **kwargs)
    except EXEC.ExecutionError as exc:
        if exc.code != code:
            _fail(f"expected {code} but got {exc.code}")
        return
    _fail(f"expected {code}")


def test_dry_run_matches_task_and_profile_and_differs_by_head() -> None:
    manifest = EXEC.benchmark_fixture.load_manifest()
    document = EXEC.dry_run(
        manifest,
        case_id=EXEC.PILOT_CASE_ID,
        manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
        profile=PROFILE,
    )
    if document["comparison"] != "COMPARABLE" or document["reason"] != "PROFILE_MATCH":
        _fail("matching profiles were not comparable")
    if document["execute_worker"] is not False or document["network"] != "NONE":
        _fail("dry-run requested execution or network")
    lanes = document["lanes"]
    if [lane["lane"] for lane in lanes] != ["CONTROL", "CANDIDATE"]:
        _fail("lanes were not control then candidate")
    if lanes[0]["worker_payload"]["task"] != lanes[1]["worker_payload"]["task"]:
        _fail("lanes did not receive the same task")
    if lanes[0]["profile"] != lanes[1]["profile"]:
        _fail("lanes did not receive the same profile")
    if lanes[0]["system_head"] == lanes[1]["system_head"]:
        _fail("lanes did not differ by system head")
    if lanes[0]["system_head"] != EXEC.benchmark_fixture.CONTROL_HEAD:
        _fail("control head drifted")
    if lanes[1]["system_head"] != EXEC.benchmark_fixture.CANDIDATE_HEAD:
        _fail("candidate head drifted")
    if lanes[0]["system_version"] != "1.6.5" or lanes[1]["system_version"] != "1.7":
        _fail("system versions drifted")
    if document["fixture_id"] != f"{EXEC.PILOT_MANIFEST_HEAD}:{EXEC.PILOT_CASE_ID}":
        _fail("fixture id drifted")
    if any(lane["task_source_head"] != EXEC.PILOT_TASK_SOURCE_HEAD for lane in lanes):
        _fail("task source drifted")


def test_worker_payload_hides_oracle_lineage_and_source_record() -> None:
    manifest = EXEC.benchmark_fixture.load_manifest()
    case = next(item for item in manifest["cases"] if item["id"] == EXEC.PILOT_CASE_ID)
    document = EXEC.dry_run(
        manifest,
        case_id=EXEC.PILOT_CASE_ID,
        manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
        profile=PROFILE,
    )
    encoded = json.dumps(document["lanes"][0]["worker_payload"])
    for banned in ("oracle", "lineage_commits", "source_record", case["source_record"], *case["oracle"], *case["lineage_commits"]):
        if banned in encoded:
            _fail(f"worker payload leaked {banned}")
    leaked = {"task": {"oracle": list(case["oracle"])}}
    try:
        EXEC._reject_worker_leak(leaked, case)
    except EXEC.ExecutionError as exc:
        if exc.code != "TASK_LEAKS_ORACLE":
            _fail(f"oracle key returned {exc.code}")
    else:
        _fail("oracle key was accepted")


def test_rejects_stale_fixture_head_and_source() -> None:
    _expect("FIXTURE_ID_INVALID", manifest_git_sha="not-a-sha")
    _expect("FIXTURE_REVISION_MISMATCH", manifest_git_sha="a" * 40)
    _expect("CASE_NOT_IN_PILOT", case_id="BENCH-DOCS-006")
    manifest = EXEC.benchmark_fixture.load_manifest()
    drifted = copy.deepcopy(manifest)
    drifted["control"]["head"] = "b" * 40
    try:
        EXEC.dry_run(
            drifted,
            case_id=EXEC.PILOT_CASE_ID,
            manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
            profile=PROFILE,
        )
    except EXEC.ExecutionError as exc:
        if exc.code != "SYSTEM_HEAD_MISMATCH":
            _fail(f"control head mismatch returned {exc.code}")
    else:
        _fail("control head mismatch was accepted")
    drifted = copy.deepcopy(manifest)
    drifted["candidate"]["head"] = "c" * 40
    try:
        EXEC.dry_run(
            drifted,
            case_id=EXEC.PILOT_CASE_ID,
            manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
            profile=PROFILE,
        )
    except EXEC.ExecutionError as exc:
        if exc.code != "SYSTEM_HEAD_MISMATCH":
            _fail(f"candidate head mismatch returned {exc.code}")
    else:
        _fail("candidate head mismatch was accepted")
    drifted = copy.deepcopy(manifest)
    for case in drifted["cases"]:
        if case["id"] == EXEC.PILOT_CASE_ID:
            case["source_commit"] = "d" * 40
    try:
        EXEC.dry_run(
            drifted,
            case_id=EXEC.PILOT_CASE_ID,
            manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
            profile=PROFILE,
        )
    except EXEC.ExecutionError as exc:
        if exc.code != "TASK_SOURCE_MISMATCH":
            _fail(f"task source mismatch returned {exc.code}")
    else:
        _fail("task source mismatch was accepted")


def test_profile_mismatch_is_not_comparable_pass() -> None:
    manifest = EXEC.benchmark_fixture.load_manifest()
    incomplete = EXEC.dry_run(
        manifest,
        case_id=EXEC.PILOT_CASE_ID,
        manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
        profile=None,
    )
    if incomplete["comparison"] != "BLOCK" or incomplete["reason"] != "PROFILE_INCOMPLETE":
        _fail("missing profile was not BLOCK")
    if incomplete["lanes"]:
        _fail("incomplete profile still emitted lanes")
    other = dict(PROFILE)
    other["toolset"] = "write"
    mismatched = EXEC.dry_run(
        manifest,
        case_id=EXEC.PILOT_CASE_ID,
        manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
        profile=PROFILE,
        candidate_profile=other,
    )
    if mismatched["comparison"] != "PARTIAL" or mismatched["reason"] != "PROFILE_MISMATCH":
        _fail("profile mismatch was not PARTIAL")
    if mismatched["comparison"] == "COMPARABLE":
        _fail("profile mismatch was a comparable pass")
    for lane in mismatched["lanes"]:
        if lane["result"]["TERMINAL"] == "PASS":
            _fail("mismatched lane reported benchmark PASS")


def test_result_skeleton_reuses_telemetry_unknowns() -> None:
    manifest = EXEC.benchmark_fixture.load_manifest()
    document = EXEC.dry_run(
        manifest,
        case_id=EXEC.PILOT_CASE_ID,
        manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
        profile=PROFILE,
    )
    for lane in document["lanes"]:
        result = lane["result"]
        if tuple(result) != EXEC.benchmark_fixture.RESULT_FIELDS:
            _fail("result skeleton dropped a #44 field")
        if result["WALL_SECONDS"] != "UNKNOWN" or result["MODEL_COST"] != "UNKNOWN":
            _fail("missing usage was estimated")
        if result["TERMINAL"] == "PASS" or result["CORRECT_BEHAVIOR"] == "PASS":
            _fail("dry-run skeleton claimed benchmark pass")
        usage = lane["telemetry"]["usage"]
        if lane["telemetry"]["semantics"] != "efficiency-telemetry":
            _fail("telemetry semantics were forked")
        if lane["telemetry"]["duration_seconds"] is not None or any(value is not None for value in usage.values()):
            _fail("telemetry estimated unexposed usage")
        if "aggregate_score" in result or "weighted_score" in result:
            _fail("result collapsed to an aggregate score")


def test_plan_has_no_production_mutation_or_execution_surface() -> None:
    manifest = EXEC.benchmark_fixture.load_manifest()
    document = EXEC.dry_run(
        manifest,
        case_id=EXEC.PILOT_CASE_ID,
        manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
        profile=PROFILE,
    )
    encoded = json.dumps(document)
    if EXEC.benchmark_fixture.PRODUCTION_MUTATION_RE.search(encoded):
        _fail("dry-run emitted a production mutation instruction")
    for lane in document["lanes"]:
        if lane["isolation"]["execute_worker"] is not False or lane["isolation"]["production_mutation"] is not False:
            _fail("isolation plan allowed worker execution or production mutation")
    text = TOOL.read_text(encoding="utf-8")
    for banned in ("subprocess", "urlopen", "urllib", "requests", "socket", "os.system", "shell=True"):
        if banned in text:
            _fail(f"dry-run tool contains {banned}")
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        status = EXEC.main(
            [
                "dry-run",
                "--provider",
                "example-provider",
                "--model",
                "example-model",
                "--reasoning",
                "low",
                "--toolset",
                "read-only",
            ]
        )
    if status != 0 or stdout.getvalue().strip() != "PASS benchmark execution dry-run":
        _fail(f"cli dry-run returned {status}: {stdout.getvalue().strip()}")


def main() -> None:
    test_dry_run_matches_task_and_profile_and_differs_by_head()
    test_worker_payload_hides_oracle_lineage_and_source_record()
    test_rejects_stale_fixture_head_and_source()
    test_profile_mismatch_is_not_comparable_pass()
    test_result_skeleton_reuses_telemetry_unknowns()
    test_plan_has_no_production_mutation_or_execution_surface()
    print("PASS benchmark execution dry-run")


if __name__ == "__main__":
    main()
