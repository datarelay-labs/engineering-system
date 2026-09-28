#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
TOOL = TOOLS / "context_canary_gate.py"
sys.path.insert(0, str(TOOLS))

import efficiency_telemetry as telemetry

spec = importlib.util.spec_from_file_location("context_canary_gate_tested", TOOL)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)

HEAD = subprocess.check_output(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
).strip()
PROFILE = {
    "provider": "provider-a",
    "model": "model-x",
    "reasoning": "medium",
    "toolset": "engineering-default",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def counts(**overrides: int) -> dict[str, int]:
    result = {field: 0 for field in telemetry.COUNT_FIELDS}
    result.update(overrides)
    return result


def validation(head: str = HEAD) -> dict[str, object]:
    return {
        "ids": ["ENG-CANARY-001"],
        "exact_head": head,
        "evidence_state": "EXACT_HEAD",
        "outcome": "PASS",
    }


def usage(cost: float = 1.0, **overrides: int | float | None) -> dict[str, int | float | None]:
    result: dict[str, int | float | None] = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 50,
        "cache_write_tokens": 10,
        "cost": cost,
    }
    result.update(overrides)
    return result


def telemetry_record(
    *,
    cost: float = 1.0,
    profile: dict[str, object] | telemetry.SessionProfile | None = None,
    terminal: str = "PASS",
    record_counts: dict[str, int] | None = None,
    record_usage: dict[str, int | float | None] | None = None,
) -> dict[str, object]:
    return telemetry.build_record(
        repo="datarelay-labs/engineering-system",
        workstream="context-live-canary",
        task_kind="DEVELOPMENT",
        profile=PROFILE if profile is None else profile,
        started_at="2026-09-28T05:00:00Z",
        finished_at="2026-09-28T05:00:05Z",
        duration_seconds=5,
        counts=counts() if record_counts is None else record_counts,
        validation=validation(),
        terminal=terminal,
        budget=telemetry.TaskBudget(None, None),
        usage=usage(cost) if record_usage is None else record_usage,
        root=ROOT,
    )


def run_record(
    arm: str,
    case_id: str,
    *,
    cost: float | str = 1.0,
    head: str = HEAD,
    terminal: str = "PASS",
    correct: str = "PASS",
    safety: str = "NO",
    exact: str = "PASS",
    retained: str = "PASS",
    retries: int = 0,
    rework: int = 0,
    human: int = 0,
) -> dict[str, object]:
    return {
        "ARM_ID": arm,
        "CASE_ID": case_id,
        "SYSTEM_HEAD": head,
        "FIXTURE_ID": f"fixture:{case_id}",
        "TERMINAL": terminal,
        "CORRECT_BEHAVIOR": correct,
        "SAFETY_REGRESSION": safety,
        "EXACT_HEAD_EVIDENCE": exact,
        "EXACT_HEAD_SUBJECT": head if exact != "MISSING" else "MISSING",
        "REQUIRED_EVIDENCE_RETAINED": retained,
        "ORIGINAL_CONTEXT_BYTES": 1000,
        "KEPT_CONTEXT_BYTES": 500 if arm != "baseline" else 1000,
        "MODEL_COST": cost,
        "RETRIES": retries,
        "REVIEW_REWORK": rework,
        "HUMAN_INTERVENTIONS": human,
    }


def run_set(records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "context-optimization-run-set",
        "records": records,
    }


def envelope(
    runs: list[dict[str, object]],
    binding_records: list[tuple[str, str, dict[str, object]]],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "context-canary-comparison",
        "run_set": run_set(runs),
        "telemetry_bindings": [
            {"arm_id": arm, "case_id": case_id, "telemetry": record}
            for arm, case_id, record in binding_records
        ],
    }


def expect_error(code: str, callback) -> None:
    try:
        callback()
    except gate.CanaryError as exc:
        if str(exc) != code:
            fail(f"expected {code}, got {exc}")
    else:
        fail(f"expected failure {code}")


def valid_document() -> dict[str, object]:
    runs = [
        run_record("baseline", "CTX-LIVE-001", cost=1.0, rework=3),
        run_record("baseline", "CTX-LIVE-002", cost=2.0),
        run_record("candidate", "CTX-LIVE-001", cost=0.8),
        run_record("candidate", "CTX-LIVE-002", cost=1.2),
    ]
    bindings = [
        (
            "baseline",
            "CTX-LIVE-001",
            telemetry_record(
                cost=1.0,
                record_counts=counts(
                    tool_turns=4,
                    rereads=1,
                    pr_rework=1,
                    ci_rework=1,
                    review_rework=1,
                ),
                record_usage=usage(
                    1.0,
                    input_tokens=1000,
                    output_tokens=100,
                    cache_read_tokens=300,
                    cache_write_tokens=40,
                ),
            ),
        ),
        (
            "baseline",
            "CTX-LIVE-002",
            telemetry_record(
                cost=2.0,
                record_counts=counts(tool_turns=5, compactions=1),
                record_usage=usage(
                    2.0,
                    input_tokens=1200,
                    output_tokens=120,
                    cache_read_tokens=350,
                    cache_write_tokens=50,
                ),
            ),
        ),
        (
            "candidate",
            "CTX-LIVE-001",
            telemetry_record(
                cost=0.8,
                record_counts=counts(tool_turns=3),
                record_usage=usage(
                    0.8,
                    input_tokens=700,
                    output_tokens=90,
                    cache_read_tokens=500,
                    cache_write_tokens=20,
                ),
            ),
        ),
        (
            "candidate",
            "CTX-LIVE-002",
            telemetry_record(
                cost=1.2,
                record_counts=counts(tool_turns=4, rereads=1),
                record_usage=usage(
                    1.2,
                    input_tokens=800,
                    output_tokens=95,
                    cache_read_tokens=550,
                    cache_write_tokens=25,
                ),
            ),
        ),
    ]
    return envelope(runs, bindings)


def report_arm(report: dict[str, object], arm_id: str) -> dict[str, object]:
    for item in report["arms"]:
        if item["arm_id"] == arm_id:
            return item
    fail(f"arm not found: {arm_id}")


def test_valid_live_canary_is_eligible_and_factual() -> None:
    report = gate.evaluate_comparison(valid_document())
    if report["decision"] != "ELIGIBLE":
        fail(f"valid canary was not eligible: {report}")
    if report["profile"] != PROFILE or report["system_head"] != HEAD:
        fail(f"profile/head binding drifted: {report}")
    if report["record_count"] != 4 or report["arm_count"] != 2:
        fail(f"report counts drifted: {report}")

    baseline = report_arm(report, "baseline")
    candidate = report_arm(report, "candidate")
    if baseline["input_tokens_total"] != 2200:
        fail(f"baseline input total wrong: {baseline}")
    if baseline["cache_read_tokens_total"] != 650:
        fail(f"baseline cache-read total wrong: {baseline}")
    if baseline["provider_cost_total"] != "3":
        fail(f"baseline provider cost wrong: {baseline}")
    if baseline["cost_per_verified_solved_task"] != "1.5":
        fail(f"baseline cost per solved wrong: {baseline}")
    if baseline["tool_turns_total"] != 9 or baseline["rereads_total"] != 1:
        fail(f"baseline count totals wrong: {baseline}")
    if (
        baseline["pr_rework_total"] != 1
        or baseline["ci_rework_total"] != 1
        or baseline["review_rework_total"] != 1
        or baseline["rework_total"] != 3
    ):
        fail(f"baseline canonical rework totals wrong: {baseline}")
    if candidate["input_tokens_total"] != 1500:
        fail(f"candidate input total wrong: {candidate}")
    if candidate["provider_cost_total"] != "2":
        fail(f"candidate provider cost wrong: {candidate}")
    if candidate["cost_per_verified_solved_task"] != "1":
        fail(f"candidate cost per solved wrong: {candidate}")

    encoded = json.dumps(report, sort_keys=True)
    for forbidden in ("winner", "ranking", "recommendation", "weighted_score"):
        if forbidden in encoded:
            fail(f"automatic optimizer judgment leaked into report: {forbidden}")


def test_profile_and_switch_mismatches_fail_closed() -> None:
    doc = valid_document()
    doc["telemetry_bindings"][2]["telemetry"]["profile"]["model"] = "model-y"
    expect_error("PROFILE_MISMATCH", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    doc["telemetry_bindings"][0]["telemetry"]["profile"]["model"] = None
    expect_error("PROFILE_UNKNOWN", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    doc["telemetry_bindings"][0]["telemetry"]["profile_switches"] = [
        {
            "field": "model",
            "from": "model-x",
            "to": "model-x",
            "justification": "TEST_SWITCH",
        }
    ]
    expect_error("PROFILE_SWITCHED", lambda: gate.evaluate_comparison(doc))


def test_binding_completeness_and_uniqueness_fail_closed() -> None:
    doc = valid_document()
    doc["telemetry_bindings"].pop()
    expect_error("TELEMETRY_BINDING_MISSING", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    doc["telemetry_bindings"].append(copy.deepcopy(doc["telemetry_bindings"][0]))
    expect_error(
        "TELEMETRY_BINDING_DUPLICATE",
        lambda: gate.evaluate_comparison(doc),
    )

    doc = valid_document()
    doc["telemetry_bindings"][1]["telemetry"]["run_id"] = (
        doc["telemetry_bindings"][0]["telemetry"]["run_id"]
    )
    expect_error(
        "TELEMETRY_RUN_ID_DUPLICATE",
        lambda: gate.evaluate_comparison(doc),
    )

    doc = valid_document()
    extra = copy.deepcopy(doc["telemetry_bindings"][0])
    extra["case_id"] = "CTX-LIVE-999"
    extra["telemetry"]["run_id"] = "f" * 32
    doc["telemetry_bindings"].append(extra)
    expect_error("TELEMETRY_BINDING_UNKNOWN", lambda: gate.evaluate_comparison(doc))


def test_multi_arm_and_head_usage_mismatches_fail_closed() -> None:
    doc = valid_document()
    doc["run_set"]["records"] = [
        item for item in doc["run_set"]["records"] if item["ARM_ID"] == "baseline"
    ]
    doc["telemetry_bindings"] = [
        item for item in doc["telemetry_bindings"] if item["arm_id"] == "baseline"
    ]
    expect_error("MULTI_ARM_REQUIRED", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    doc["telemetry_bindings"][0]["telemetry"]["validation"]["exact_head"] = "b" * 40
    expect_error("TELEMETRY_HEAD_MISMATCH", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    for run in doc["run_set"]["records"]:
        if run["ARM_ID"] == "candidate":
            run["SYSTEM_HEAD"] = "b" * 40
            run["EXACT_HEAD_SUBJECT"] = "b" * 40
    expect_error("SYSTEM_HEAD_MISMATCH", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    doc["telemetry_bindings"][0]["telemetry"]["usage"]["cache_read_tokens"] = None
    expect_error("USAGE_INCOMPLETE", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    doc["telemetry_bindings"][0]["telemetry"]["usage"]["cost"] = None
    expect_error("USAGE_INCOMPLETE", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    doc["telemetry_bindings"][0]["telemetry"]["terminal"] = "FAIL"
    doc["telemetry_bindings"][0]["telemetry"]["validation"]["evidence_state"] = "STALE"
    expect_error(
        "TELEMETRY_EXACT_HEAD_REQUIRED",
        lambda: gate.evaluate_comparison(doc),
    )

    doc = valid_document()
    doc["run_set"]["records"][2]["MODEL_COST"] = "UNKNOWN"
    expect_error("MODEL_COST_REQUIRED", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    doc["run_set"]["records"][2]["MODEL_COST"] = 0.9
    expect_error("MODEL_COST_MISMATCH", lambda: gate.evaluate_comparison(doc))


def test_outcome_and_effort_alignment_fail_closed() -> None:
    doc = valid_document()
    doc["run_set"]["records"][2]["SAFETY_REGRESSION"] = "YES"
    expect_error("VERIFIED_OUTCOME_INCOMPLETE", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    doc["run_set"]["records"][2]["RETRIES"] = 1
    expect_error("RETRIES_MISMATCH", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    doc["run_set"]["records"][2]["REVIEW_REWORK"] = 1
    expect_error("REVIEW_REWORK_MISMATCH", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    doc["run_set"]["records"][2]["HUMAN_INTERVENTIONS"] = 1
    expect_error("HUMAN_INTERVENTIONS_MISMATCH", lambda: gate.evaluate_comparison(doc))

    doc = valid_document()
    doc["run_set"]["records"][2]["TERMINAL"] = "FAIL"
    doc["run_set"]["records"][2]["CORRECT_BEHAVIOR"] = "FAIL"
    expect_error("TERMINAL_MISMATCH", lambda: gate.evaluate_comparison(doc))


def test_benchmark_case_set_gate_is_reused() -> None:
    doc = valid_document()
    doc["run_set"]["records"] = [
        item
        for item in doc["run_set"]["records"]
        if not (item["ARM_ID"] == "candidate" and item["CASE_ID"] == "CTX-LIVE-002")
    ]
    doc["telemetry_bindings"] = [
        item
        for item in doc["telemetry_bindings"]
        if not (item["arm_id"] == "candidate" and item["case_id"] == "CTX-LIVE-002")
    ]
    expect_error(
        "BENCHMARK_INVALID:ARM_CASE_SET_MISMATCH",
        lambda: gate.evaluate_comparison(doc),
    )


def test_telemetry_privacy_contract_cannot_be_bypassed() -> None:
    doc = valid_document()
    doc["telemetry_bindings"][0]["telemetry"]["prompt"] = "hidden prompt"
    expect_error(
        "TELEMETRY_INVALID:PROHIBITED_FIELD",
        lambda: gate.evaluate_comparison(doc),
    )

    doc = valid_document()
    doc["telemetry_bindings"][0]["telemetry"]["profile"]["toolset"] = "/tmp/private"
    expect_error(
        "TELEMETRY_INVALID:LOCAL_PATH_PROHIBITED",
        lambda: gate.evaluate_comparison(doc),
    )


def test_schema_and_cli_are_deterministic() -> None:
    doc = valid_document()
    leaked = copy.deepcopy(doc)
    leaked["extra"] = True
    expect_error("ENVELOPE_INVALID", lambda: gate.evaluate_comparison(leaked))

    first = json.dumps(
        gate.evaluate_comparison(doc),
        sort_keys=True,
        separators=(",", ":"),
    )
    second = json.dumps(
        gate.evaluate_comparison(doc),
        sort_keys=True,
        separators=(",", ":"),
    )
    if first != second:
        fail("same live-canary facts produced nondeterministic report")

    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "input.json"
        input_path.write_text(json.dumps(doc), encoding="utf-8")
        run = subprocess.run(
            [sys.executable, str(TOOL), "--input", str(input_path)],
            text=True,
            capture_output=True,
            check=False,
        )
        if run.returncode != 0:
            fail(f"CLI eligible path failed: {run.stdout} {run.stderr}")
        cli_report = json.loads(run.stdout)
        if cli_report["decision"] != "ELIGIBLE":
            fail(f"CLI emitted wrong decision: {cli_report}")


def test_source_has_no_provider_or_network_dependency() -> None:
    source = TOOL.read_text(encoding="utf-8")
    for forbidden in (
        "import requests",
        "import urllib",
        "import socket",
        "openai",
        "anthropic",
        "http://",
        "https://",
    ):
        if forbidden in source:
            fail(f"canary gate gained provider/network dependency: {forbidden}")


def main() -> int:
    tests = [
        test_valid_live_canary_is_eligible_and_factual,
        test_profile_and_switch_mismatches_fail_closed,
        test_binding_completeness_and_uniqueness_fail_closed,
        test_multi_arm_and_head_usage_mismatches_fail_closed,
        test_outcome_and_effort_alignment_fail_closed,
        test_benchmark_case_set_gate_is_reused,
        test_telemetry_privacy_contract_cannot_be_bypassed,
        test_schema_and_cli_are_deterministic,
        test_source_has_no_provider_or_network_dependency,
    ]
    for test in tests:
        test()
    print("CONTEXT_CANARY_GATE_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
