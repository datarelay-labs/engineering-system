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



def test_campaign_prepares_all_frozen_cases_without_running_workers() -> None:
    manifest = EXEC.benchmark_fixture.load_manifest()
    plan = EXEC.campaign_dry_run(
        manifest, manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD, profile=PROFILE,
    )
    if plan["kind"] != "benchmark-campaign-dry-run" or plan["execution_state"] != "NOT_EXECUTED":
        _fail("campaign implied an executed model run")
    if plan["case_count"] != 7 or plan["observed_results"] != 0:
        _fail("campaign did not keep seven plans separate from actual results")
    if plan["profile_parity"] != "DECLARATIONS_MATCH" or plan["execute_worker"] is not False:
        _fail("campaign did not enforce matched declared profiles")
    expected = EXEC.benchmark_fixture.REQUIRED_CASE_IDS
    if tuple(case["case_id"] for case in plan["cases"]) != expected:
        _fail("campaign case ordering or set drifted")
    for entry in plan["cases"]:
        lanes = entry["lanes"]
        if tuple(lane["lane"] for lane in lanes) != ("CONTROL", "CANDIDATE"):
            _fail("campaign lane identity drifted")
        if lanes[0]["worker_payload"]["task"] != lanes[1]["worker_payload"]["task"]:
            _fail("candidate task differs from control")
        if lanes[0]["profile"] != lanes[1]["profile"]:
            _fail("candidate profile differs from control")
        if any(lane["isolation"]["execute_worker"] for lane in lanes):
            _fail("campaign attempted worker execution")
        if any(EXEC.accepts_final_result(lane["result_template"]) for lane in lanes):
            _fail("campaign converted template to final evidence")
        source = next(c for c in manifest["cases"] if c["id"] == entry["case_id"])
        payload = json.dumps(lanes[0]["worker_payload"])
        if any(key in payload for key in ("source_record", "lineage_commits", "\"oracle\"")):
            _fail("campaign exposed auditor-only material to worker")
        if source["source_record"] in payload or any(code in payload for code in source["oracle"]):
            _fail("campaign leaked frozen oracle")

    multi = next(c for c in manifest["cases"] if c["id"] == "BENCH-MULTI-007")
    control = next(c for c in plan["cases"] if c["case_id"] == "BENCH-MULTI-007")["lanes"][0]
    if control["system_head"] != multi["lineage_commits"][0]:
        _fail("historical comparison false-positive fixture was not reproduced")
    evil = copy.deepcopy(control["worker_payload"])
    evil["task"]["objective"] = multi["lineage_commits"][0]
    try:
        EXEC._reject_worker_leak(evil, multi)
    except EXEC.ExecutionError as exc:
        if exc.code != "TASK_LEAKS_ORACLE":
            _fail("task-side historical lineage was not rejected")
    else:
        _fail("campaign allowed a hidden lineage SHA in the worker task")
    evil = copy.deepcopy(control["worker_payload"])
    evil["run"]["alternate_head"] = multi["lineage_commits"][0]
    try:
        EXEC._reject_worker_leak(evil, multi)
    except EXEC.ExecutionError as exc:
        if exc.code != "TASK_LEAKS_ORACLE":
            _fail("alternate run field evaded lineage guard")
    else:
        _fail("campaign allowed hidden lineage outside fixed system HEAD")

    mismatch = EXEC.campaign_dry_run(
        manifest, manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD, profile=PROFILE,
        candidate_profile=dict(PROFILE, toolset="other"),
    )
    if mismatch["profile_parity"] != "DECLARATIONS_DIFFER" or mismatch["execution_state"] != "NOT_EXECUTED":
        _fail("mismatched campaign profiles were treated as equivalent")
    missing = EXEC.campaign_dry_run(
        manifest, manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD,
        profile=dict(PROFILE, model=None),
    )
    if missing["profile_parity"] != "PROFILE_INCOMPLETE" or missing["cases"]:
        _fail("incomplete campaign profile did not fail closed")
    drift = copy.deepcopy(manifest)
    drift["cases"][1]["task"]["objective"] = "A different accepted objective."
    try:
        EXEC.campaign_dry_run(drift, manifest_git_sha=EXEC.PILOT_MANIFEST_HEAD, profile=PROFILE)
    except EXEC.ExecutionError as exc:
        if exc.code != "FROZEN_MANIFEST_MISMATCH":
            _fail(f"campaign drift gave {exc.code}")
    else:
        _fail("edited campaign objective retained frozen fixture identity")


def test_campaign_cli_prepares_seven_non_final_plans() -> None:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        status = EXEC.main([
            "campaign-dry-run", "--provider", "example-provider",
            "--model", "example-model", "--reasoning", "low",
            "--toolset", "read-only", "--json",
        ])
    if status != 0:
        _fail("campaign CLI failed with a matching declared profile")
    plan = json.loads(stdout.getvalue())
    if plan["execution_state"] != "NOT_EXECUTED" or len(plan["cases"]) != 7:
        _fail("campaign CLI claimed executed model evidence or lost cases")


def test_campaign_cli_incomplete_profile_reports_zero_prepared() -> None:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        status = EXEC.main([
            "campaign-dry-run", "--provider", "example-provider",
            "--reasoning", "low", "--toolset", "read-only",
        ])
    if status != 1 or "BLOCK benchmark campaign dry-run 0/7 planned" not in stdout.getvalue():
        _fail("incomplete profile inaccurately reported all 7 campaign tasks as prepared")


def _campaign_records() -> dict:
    manifest = EXEC.benchmark_fixture.load_manifest()
    records = []
    for case in manifest["cases"]:
        for lane in ("CONTROL", "CANDIDATE"):
            record = _final_example(lane)
            record.update(
                CASE_ID=case["id"],
                FIXTURE_ID=f"{EXEC.PILOT_MANIFEST_HEAD}:{case['id']}",
                TERMINAL="PASS",
                CORRECT_BEHAVIOR="PASS",
                REGRESSION_TESTS="PASS",
                EXACT_HEAD_EVIDENCE="PASS",
                RELEVANT_CONTEXT="BOUNDED",
                NOTES_CODE="SYNTHETIC_TEST_ONLY",
            )
            records.append(record)
    return {
        "schema_version": 1,
        "kind": "benchmark-campaign-records",
        "manifest_git_sha": EXEC.PILOT_MANIFEST_HEAD,
        "records": records,
    }


def test_campaign_result_inspection_never_grants_verified_outcomes() -> None:
    manifest = EXEC.benchmark_fixture.load_manifest()
    raw = _campaign_records()  # Synthetic values are a shape test only.
    report = EXEC.inspect_campaign_results(raw, manifest=manifest)
    if report["kind"] != "benchmark-campaign-record-inspection":
        _fail("campaign result report type drifted")
    if report["structural_status"] != "COMPLETE" or report["paired_cases"] != 7:
        _fail("seven complete shape-valid record pairs were rejected")
    if report["record_count"] != 14 or report["case_count"] != 7:
        _fail("campaign result lost exact seven-case counts")
    if report["execution_attestation"] != "UNVERIFIED":
        _fail("self-asserted records were mistaken for model execution evidence")
    if report["native_usage_attestation"] != "UNVERIFIED":
        _fail("self-asserted model cost was mistaken for native provider accounting")
    if report["quality_closure"] != "BLOCK" or report["release_authorized"] is not False:
        _fail("shape-valid synthetic data granted release or user-gate authority")
    if any(key in json.dumps(report) for key in ('"oracle"', '"lineage_commits"', "SYNTHETIC_TEST_ONLY")):
        _fail("record report leaked source oracle or raw notes")

    partial = copy.deepcopy(raw)
    partial["records"] = partial["records"][:1]
    incomplete = EXEC.inspect_campaign_results(partial, manifest=manifest)
    if incomplete["structural_status"] != "PARTIAL" or incomplete["paired_cases"] != 0:
        _fail("partial campaign falsely reported complete")
    if incomplete["missing_lane_count"] != 13 or incomplete["quality_closure"] != "BLOCK":
        _fail("partial campaign did not report missing lanes")


def test_campaign_result_inspection_rejects_false_provenance_and_identity() -> None:
    manifest = EXEC.benchmark_fixture.load_manifest()
    baseline = _campaign_records()

    def reject(mutant: dict, code: str) -> None:
        try:
            EXEC.inspect_campaign_results(mutant, manifest=manifest)
        except EXEC.ExecutionError as exc:
            if exc.code != code:
                _fail(f"wrong campaign rejection {exc.code}, expected {code}")
        else:
            _fail(f"campaign accepted prohibited input: {code}")

    duplicate = copy.deepcopy(baseline)
    duplicate["records"][1] = duplicate["records"][0]
    reject(duplicate, "DUPLICATE_CAMPAIGN_LANE")

    swapped = copy.deepcopy(baseline)
    swapped["records"][1]["SYSTEM_HEAD"] = EXEC.benchmark_fixture.CONTROL_HEAD
    reject(swapped, "CAMPAIGN_RECORD_SCHEMA_INVALID")

    stale = copy.deepcopy(baseline)
    stale["records"][2]["FIXTURE_ID"] = f"{'a'*40}:BENCH-CROSS-002"
    reject(stale, "CAMPAIGN_FIXTURE_MISMATCH")

    mixed = copy.deepcopy(baseline)
    mixed["records"][3]["FIXTURE_ID"] = f"{EXEC.PILOT_MANIFEST_HEAD}:BENCH-BUG-001"
    reject(mixed, "CAMPAIGN_FIXTURE_MISMATCH")

    rogue = copy.deepcopy(baseline)
    rogue["records"][0]["CASE_ID"] = "BENCH-ROGUE-999"
    reject(rogue, "CAMPAIGN_RECORD_SCHEMA_INVALID")

    placeholder = copy.deepcopy(baseline)
    placeholder["records"][0]["NOTES_CODE"] = "NOT_EXECUTED"
    reject(placeholder, "CAMPAIGN_UNEXECUTED_PLACEHOLDER")

    claimed = copy.deepcopy(baseline)
    claimed["verified"] = True
    reject(claimed, "CAMPAIGN_ENVELOPE_INVALID")

    wrong_manifest = copy.deepcopy(baseline)
    wrong_manifest["manifest_git_sha"] = "b" * 40
    reject(wrong_manifest, "FIXTURE_REVISION_MISMATCH")

    oversized = copy.deepcopy(baseline)
    oversized["records"].append(oversized["records"][0])
    reject(oversized, "CAMPAIGN_RECORD_COUNT_EXCEEDED")

    for invalid_cost in (float("nan"), float("inf"), float("-inf")):
        nonfinite = copy.deepcopy(baseline)
        nonfinite["records"][0]["MODEL_COST"] = invalid_cost
        reject(nonfinite, "CAMPAIGN_RECORD_SCHEMA_INVALID")

    contradictory = copy.deepcopy(baseline)
    contradictory["records"][0]["CORRECT_BEHAVIOR"] = "FAIL"
    reject(contradictory, "CAMPAIGN_CONTRADICTORY_TERMINAL")
    unsafe = copy.deepcopy(baseline)
    unsafe["records"][0]["SAFETY_REGRESSION"] = "YES"
    reject(unsafe, "CAMPAIGN_CONTRADICTORY_TERMINAL")


def test_campaign_record_inspection_cli_stays_evidence_only() -> None:
    import tempfile
    raw = _campaign_records()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "synthetic-campaign.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = EXEC.main(["campaign-inspect", "--input", str(path), "--json"])
        if status != 0:
            _fail("complete synthetic records should pass structural inspection only")
        report = json.loads(stdout.getvalue())
        if report["quality_closure"] != "BLOCK" or report["execution_attestation"] != "UNVERIFIED":
            _fail("campaign CLI promoted synthetic results to verified outcomes")

        bad = copy.deepcopy(raw)
        bad["records"][0]["NOTES_CODE"] = "NOT_EXECUTED"
        path.write_text(json.dumps(bad), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = EXEC.main(["campaign-inspect", "--input", str(path)])
        if status == 0 or "FAIL CAMPAIGN_UNEXECUTED_PLACEHOLDER" not in stdout.getvalue():
            _fail("campaign CLI accepted a dry-run placeholder as an observed result")

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
    test_campaign_prepares_all_frozen_cases_without_running_workers()
    test_campaign_cli_prepares_seven_non_final_plans()
    test_campaign_cli_incomplete_profile_reports_zero_prepared()
    test_campaign_result_inspection_never_grants_verified_outcomes()
    test_campaign_result_inspection_rejects_false_provenance_and_identity()
    test_campaign_record_inspection_cli_stays_evidence_only()
    print("PASS benchmark execution dry-run")


if __name__ == "__main__":
    main()
