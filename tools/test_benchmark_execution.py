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
        if lane["result_template"]["final"] is not False or lane["result_template"]["fields"]["TERMINAL"] is not None:
            _fail("mismatched lane reported a final benchmark terminal")
        if EXEC.accepts_final_result(lane["result_template"]) or EXEC.accepts_final_result(lane["result_template"]["fields"]):
            _fail("mismatched template was accepted as a final result")


def _final_example(lane: str = "CONTROL") -> dict:
    if lane == "CANDIDATE":
        version = "1.7"
        head = EXEC.benchmark_fixture.CANDIDATE_HEAD
    else:
        version = "1.6.5"
        head = EXEC.benchmark_fixture.CONTROL_HEAD
    return {
        "CASE_ID": "BENCH-BUG-001",
        "SYSTEM_VERSION": version,
        "SYSTEM_HEAD": head,
        "FIXTURE_ID": f"{EXEC.PILOT_MANIFEST_HEAD}:BENCH-BUG-001",
        "TERMINAL": "BLOCK",
        "CORRECT_BEHAVIOR": "FAIL",
        "SAFETY_REGRESSION": "NO",
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
        "NOTES_CODE": "NOT_EXECUTED",
    }


def test_result_template_cannot_pass_as_final_result() -> None:
    manifest = EXEC.benchmark_fixture.load_manifest()
    document = EXEC.dry_run(
        manifest,
        case_id=EXEC.PILOT_CASE_ID,
        manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
        profile=PROFILE,
    )
    if EXEC.accepts_final_result(document):
        _fail("dry-run document was accepted as a final result")
    for lane in document["lanes"]:
        template = lane["result_template"]
        fields = template["fields"]
        if template["kind"] != "benchmark-result-template" or template["final"] is not False:
            _fail("result template was not marked non-final")
        if tuple(fields) != EXEC.benchmark_fixture.RESULT_FIELDS:
            _fail("result template dropped a #44 field")
        if fields["WALL_SECONDS"] != "UNKNOWN" or fields["MODEL_COST"] != "UNKNOWN":
            _fail("unavailable cost or time was estimated")
        for name in (
            "TERMINAL",
            "CORRECT_BEHAVIOR",
            "SAFETY_REGRESSION",
            "REGRESSION_TESTS",
            "EXACT_HEAD_EVIDENCE",
            "RELEVANT_CONTEXT",
            "RETRIES",
            "REREADS",
            "COMPACTIONS",
            "REVIEW_REWORK",
            "HUMAN_INTERVENTIONS",
            "HOST_RESOURCE_OUTCOME",
            "NOTES_CODE",
        ):
            if fields[name] is not None:
                _fail(f"template invented {name}")
        if EXEC.accepts_final_result(template) or EXEC.accepts_final_result(fields):
            _fail("non-final template was accepted as a final result")
        extended = dict(fields)
        extended["CORRECT_BEHAVIOR"] = "UNKNOWN"
        extended["SAFETY_REGRESSION"] = "UNKNOWN"
        if EXEC.accepts_final_result(extended):
            _fail("final contract accepted UNKNOWN behavior values")
        if "aggregate_score" in fields or "weighted_score" in fields:
            _fail("result collapsed to an aggregate score")
    if not EXEC.accepts_final_result(_final_example()):
        _fail("final result contract rejected a complete #44 record")
    if not EXEC.accepts_final_result(_final_example("CANDIDATE")):
        _fail("final result contract rejected the candidate lane identity")


def test_final_result_rejects_unbound_identity() -> None:
    control = _final_example()
    wrong_case = dict(control)
    wrong_case["CASE_ID"] = "BENCH-DOCS-006"
    if EXEC.accepts_final_result(wrong_case):
        _fail("final result accepted a different case")
    wrong_fixture = dict(control)
    wrong_fixture["FIXTURE_ID"] = f"{'a' * 40}:BENCH-BUG-001"
    if EXEC.accepts_final_result(wrong_fixture):
        _fail("final result accepted an unrelated fixture")
    mismatched_fixture = dict(control)
    mismatched_fixture["FIXTURE_ID"] = f"{EXEC.PILOT_MANIFEST_HEAD}:BENCH-DOCS-006"
    if EXEC.accepts_final_result(mismatched_fixture):
        _fail("final result accepted a case/fixture mismatch")
    unrelated_head = dict(control)
    unrelated_head["SYSTEM_HEAD"] = "b" * 40
    if EXEC.accepts_final_result(unrelated_head):
        _fail("final result accepted an unrelated system head")
    swapped = dict(control)
    swapped["SYSTEM_HEAD"] = EXEC.benchmark_fixture.CANDIDATE_HEAD
    if EXEC.accepts_final_result(swapped):
        _fail("final result accepted a control/candidate identity swap")
    swapped_version = dict(control)
    swapped_version["SYSTEM_VERSION"] = "1.7"
    if EXEC.accepts_final_result(swapped_version):
        _fail("final result accepted a swapped system version")


def _telemetry_record(**overrides: object):
    counts = overrides.pop("counts", None)
    if counts is None:
        counts = {field: 0 for field in EXEC.efficiency_telemetry.COUNT_FIELDS}
    validation = {
        "ids": ["ENG-BENCH-EXEC-001"],
        "exact_head": None,
        "evidence_state": "MISSING",
        "outcome": "UNKNOWN",
    }
    extra_validation = overrides.pop("validation", None)
    if isinstance(extra_validation, dict):
        validation.update(extra_validation)
    kwargs = {
        "repo": EXEC.PILOT_REPOSITORY,
        "workstream": EXEC.LANE_WORKSTREAM["CONTROL"],
        "task_kind": "TEST",
        "profile": dict(PROFILE),
        "started_at": "2026-09-26T00:00:00Z",
        "finished_at": None,
        "duration_seconds": None,
        "counts": counts,
        "validation": validation,
        "terminal": "BLOCK",
        "budget": {
            "soft_limit": None,
            "consumed": None,
            "unit": None,
            "state": "UNKNOWN",
            "disposition": "CONTINUE",
        },
        "usage": None,
        "run_id": "a" * 32,
    }
    kwargs.update(overrides)
    return EXEC.efficiency_telemetry.build_record(**kwargs)


def _derive(record: dict, **overrides: object):
    kwargs = {
        "lane": "CONTROL",
        "system_head": EXEC.benchmark_fixture.CONTROL_HEAD,
        "profile": dict(PROFILE),
        "run_id": record.get("run_id", "a" * 32),
    }
    kwargs.update(overrides)
    return EXEC.derive_observed_fields(record, **kwargs)


def test_frozen_manifest_content_must_match_revision() -> None:
    manifest = EXEC.benchmark_fixture.load_manifest()
    mutated = copy.deepcopy(manifest)
    mutated["cases"][0]["title"] = "Modified otherwise-valid title"
    try:
        EXEC.dry_run(
            mutated,
            case_id=EXEC.PILOT_CASE_ID,
            manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
            profile=PROFILE,
        )
    except EXEC.ExecutionError as exc:
        if exc.code != "FROZEN_MANIFEST_MISMATCH":
            _fail(f"modified frozen manifest returned {exc.code}")
    else:
        _fail("modified manifest inherited the frozen manifest SHA")


def test_telemetry_lane_binding_rejects_swaps_and_unrelated_records() -> None:
    record = _telemetry_record(run_id="c" * 32)
    exact = copy.deepcopy(record)
    exact["validation"]["evidence_state"] = "EXACT_HEAD"
    exact["validation"]["exact_head"] = EXEC.benchmark_fixture.CONTROL_HEAD
    derived = _derive(exact)
    if derived["EXACT_HEAD_EVIDENCE"] != "PASS":
        _fail("matching control lane did not derive exact-head evidence")
    try:
        _derive(
            exact,
            lane="CANDIDATE",
            system_head=EXEC.benchmark_fixture.CANDIDATE_HEAD,
        )
    except EXEC.ExecutionError as exc:
        if exc.code != "TELEMETRY_LANE_MISMATCH":
            _fail(f"control record on candidate lane returned {exc.code}")
    else:
        _fail("control telemetry was accepted for the candidate lane")
    swapped_head = copy.deepcopy(exact)
    swapped_head["validation"]["exact_head"] = EXEC.benchmark_fixture.CANDIDATE_HEAD
    swapped_head["workstream"] = EXEC.LANE_WORKSTREAM["CANDIDATE"]
    try:
        _derive(swapped_head)
    except EXEC.ExecutionError as exc:
        if exc.code != "TELEMETRY_LANE_MISMATCH":
            _fail(f"swapped exact head returned {exc.code}")
    else:
        _fail("candidate exact head was accepted for the control lane")
    unrelated = copy.deepcopy(record)
    unrelated["repo"] = "example/unrelated"
    try:
        _derive(unrelated)
    except EXEC.ExecutionError as exc:
        if exc.code != "TELEMETRY_LANE_MISMATCH":
            _fail(f"unrelated repository returned {exc.code}")
    else:
        _fail("unrelated telemetry record was accepted")
    try:
        _derive(record, run_id="d" * 32)
    except EXEC.ExecutionError as exc:
        if exc.code != "TELEMETRY_LANE_MISMATCH":
            _fail(f"unrelated run id returned {exc.code}")
    else:
        _fail("unrelated run id was accepted")
    other_profile = dict(PROFILE)
    other_profile["model"] = "other-model"
    try:
        _derive(record, profile=other_profile)
    except EXEC.ExecutionError as exc:
        if exc.code != "TELEMETRY_LANE_MISMATCH":
            _fail(f"profile mismatch returned {exc.code}")
    else:
        _fail("telemetry profile mismatch was accepted")


def test_telemetry_mapping_uses_canonical_records_only() -> None:
    manifest = EXEC.benchmark_fixture.load_manifest()
    document = EXEC.dry_run(
        manifest,
        case_id=EXEC.PILOT_CASE_ID,
        manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
        profile=PROFILE,
    )
    encoded = json.dumps(document)
    if '"kind": "efficiency-telemetry"' in encoded or '"semantics": "efficiency-telemetry"' in encoded:
        _fail("dry-run emitted a telemetry record")
    mapping = document["telemetry_mapping"]
    if mapping["authority"] != "tools/efficiency_telemetry.py":
        _fail("telemetry mapping left the canonical tool")
    if mapping["schema"] != "schemas/efficiency-telemetry.schema.json":
        _fail("telemetry mapping left the canonical schema")
    counts = {field: 0 for field in EXEC.efficiency_telemetry.COUNT_FIELDS}
    counts["retries"] = 2
    record = _telemetry_record(counts=counts, run_id="a" * 32)
    if record["kind"] != "efficiency-telemetry":
        _fail("canonical telemetry record was not built")
    derived = _derive(record)
    if derived["WALL_SECONDS"] != "UNKNOWN" or derived["MODEL_COST"] != "UNKNOWN":
        _fail("canonical null usage was estimated")
    if derived["RETRIES"] != 2 or derived["EXACT_HEAD_EVIDENCE"] != "MISSING":
        _fail("canonical telemetry fields were not derived")
    for name in ("CORRECT_BEHAVIOR", "SAFETY_REGRESSION", "TERMINAL", "NOTES_CODE"):
        if name in derived:
            _fail(f"derivation invented evaluator field {name}")
    partial = {"semantics": "efficiency-telemetry", "duration_seconds": None}
    try:
        _derive(partial)
    except EXEC.ExecutionError:
        return
    _fail("partial telemetry object was accepted")


def test_review_rework_preserves_canonical_aggregate() -> None:
    counts = {field: 0 for field in EXEC.efficiency_telemetry.COUNT_FIELDS}
    counts["pr_rework"] = 2
    counts["ci_rework"] = 3
    counts["review_rework"] = 5
    record = _telemetry_record(counts=counts, run_id="b" * 32)
    derived = _derive(record)
    report = EXEC.efficiency_telemetry.build_report(
        [record],
        head="a" * 40,
        evidence_state="MISSING",
    )
    if derived["REVIEW_REWORK"] != report["rework_count"]:
        _fail("benchmark REVIEW_REWORK diverged from canonical rework_count")
    if derived["REVIEW_REWORK"] != 10 or derived["REVIEW_REWORK"] == counts["review_rework"]:
        _fail("REVIEW_REWORK omitted pr or ci rework")
    review = next(item for item in EXEC.telemetry_mapping()["derivations"] if item["result_field"] == "REVIEW_REWORK")
    if review.get("aggregate") != "efficiency_telemetry.rework_count" or review.get("source") != "rework_count":
        _fail("REVIEW_REWORK mapping is not the canonical aggregate")
    if "counts.review_rework" in json.dumps(EXEC.telemetry_mapping()):
        _fail("REVIEW_REWORK still maps only review_rework")


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
    test_result_template_cannot_pass_as_final_result()
    test_final_result_rejects_unbound_identity()
    test_frozen_manifest_content_must_match_revision()
    test_telemetry_lane_binding_rejects_swaps_and_unrelated_records()
    test_telemetry_mapping_uses_canonical_records_only()
    test_review_rework_preserves_canonical_aggregate()
    test_plan_has_no_production_mutation_or_execution_surface()
    print("PASS benchmark execution dry-run")


if __name__ == "__main__":
    main()
