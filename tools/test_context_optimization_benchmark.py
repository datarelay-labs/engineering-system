#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/context_optimization_benchmark.py"
FIXTURE_PATH = ROOT / "evals/context-optimization/fixtures.json"
spec = importlib.util.spec_from_file_location("context_optimization_benchmark", MODULE_PATH)
assert spec and spec.loader
bench = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bench
spec.loader.exec_module(bench)


def fail(message: str) -> None:
    raise AssertionError(message)


def record(
    arm: str,
    case_id: str,
    *,
    terminal: str = "PASS",
    correct: str = "PASS",
    safety: str = "NO",
    exact: str = "PASS",
    retained: str = "PASS",
    original: int = 1000,
    kept: int = 500,
    cost: float | int | str = "UNKNOWN",
    retries: int = 0,
    rework: int = 0,
    human: int = 0,
    head: str = "a" * 40,
) -> dict[str, object]:
    return {
        "ARM_ID": arm,
        "CASE_ID": case_id,
        "SYSTEM_HEAD": head,
        "FIXTURE_ID": (
            f"{bench.benchmark_execution.PILOT_MANIFEST_HEAD}:{case_id}"
            if case_id.startswith("BENCH-") else f"fixture:{case_id}"
        ),
        "TERMINAL": terminal,
        "CORRECT_BEHAVIOR": correct,
        "SAFETY_REGRESSION": safety,
        "EXACT_HEAD_EVIDENCE": exact,
        "EXACT_HEAD_SUBJECT": head if exact != "MISSING" else "MISSING",
        "REQUIRED_EVIDENCE_RETAINED": retained,
        "ORIGINAL_CONTEXT_BYTES": original,
        "KEPT_CONTEXT_BYTES": kept,
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


def arm(result: dict[str, object], arm_id: str) -> dict[str, object]:
    for item in result["arms"]:
        if item["arm_id"] == arm_id:
            return item
    fail(f"arm not found: {arm_id}")


def test_frozen_fixture_manifest_and_retention() -> None:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    result = bench.evaluate_manifest(raw)
    if result["decision"] != "PASS" or result["case_count"] != 3:
        fail(f"fixture report failed: {result}")
    expected = {
        "CTX-STALE-001": (926, ["goal", "next", "identity", "relevant-code"]),
        "CTX-RELEVANCE-002": (462, ["state", "code"]),
        "CTX-SECURITY-003": (856, ["security", "evidence", "failure", "audit-code"]),
    }
    for item in result["cases"]:
        budget, selected = expected[item["case_id"]]
        if item["budget_bytes"] != budget or item["kept_bytes"] != budget:
            fail(f"fixture budget drift: {item}")
        if item["selected_ids"] != selected:
            fail(f"fixture selected set drift: {item}")
        if item["reduction_ratio"] <= 0:
            fail(f"fixture did not reduce optional context: {item}")
    report_text = json.dumps(result, sort_keys=True)
    for forbidden in (
        "TRUST_BOUNDARY_UNAVAILABLE",
        "ci:176",
        "historical unrelated build output",
        "/home/",
    ):
        if forbidden in report_text:
            fail(f"fixture report leaked source content/reference: {forbidden}")


def test_fixture_expected_ids_form_complete_partition() -> None:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    raw["cases"][0]["blocks"].append({
        "id": "undeclared-optional",
        "kind": "docs",
        "text": "unrelated context that must be explicitly classified",
        "reference": "docs:undeclared",
        "protected": False,
        "priority": 100,
    })
    try:
        bench.validate_manifest(raw)
    except bench.BenchmarkError as exc:
        if str(exc) != "CASE_EXPECTED_ID_PARTITION_INVALID":
            fail(f"wrong incomplete partition rejection: {exc}")
    else:
        fail("fixture accepted a block missing from keep/drop expectations")


def test_fixture_report_is_byte_stable() -> None:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    first = json.dumps(bench.evaluate_manifest(raw), sort_keys=True, separators=(",", ":"))
    second = json.dumps(bench.evaluate_manifest(raw), sort_keys=True, separators=(",", ":"))
    if first != second:
        fail("fixture report was not deterministic")


def test_unknown_cost_stays_unknown() -> None:
    result = bench.score_run_set(run_set([
        record("compiler", "BENCH-BUG-001", cost=1.25),
        record("compiler", "BENCH-CLI-004", cost="UNKNOWN", original=1200, kept=600),
    ]))
    item = arm(result, "compiler")
    if item["verified_solved_count"] != 2:
        fail(f"verified solved count wrong: {item}")
    if item["model_cost_total"] != "UNKNOWN" or item["cost_per_verified_solved_task"] != "UNKNOWN":
        fail(f"unknown model cost was estimated: {item}")
    if item["cost_status_reason"] != "MODEL_COST_INCOMPLETE":
        fail(f"wrong unknown-cost reason: {item}")


def test_measured_cost_per_verified_solved_task() -> None:
    result = bench.score_run_set(run_set([
        record("baseline", "BENCH-BUG-001", cost=2.5, original=1000, kept=1000, retries=1),
        record("baseline", "BENCH-CLI-004", cost=3.5, terminal="FAIL", correct="FAIL", original=1200, kept=1200, rework=2),
        record("compiler", "BENCH-BUG-001", cost=1.0, original=1000, kept=400),
        record("compiler", "BENCH-CLI-004", cost=2.0, original=1200, kept=500, human=1),
    ]))
    baseline = arm(result, "baseline")
    compiler = arm(result, "compiler")
    if baseline["verified_solved_count"] != 1:
        fail(f"failed run counted as solved: {baseline}")
    if baseline["model_cost_total"] != "6" or baseline["cost_per_verified_solved_task"] != "6":
        fail(f"baseline economics wrong: {baseline}")
    if baseline["retries_total"] != 1 or baseline["review_rework_total"] != 2:
        fail(f"baseline effort totals wrong: {baseline}")
    if compiler["verified_solved_count"] != 2:
        fail(f"compiler solved count wrong: {compiler}")
    if compiler["model_cost_total"] != "3" or compiler["cost_per_verified_solved_task"] != "1.5":
        fail(f"compiler economics wrong: {compiler}")
    if compiler["human_interventions_total"] != 1:
        fail(f"human intervention total wrong: {compiler}")
    if compiler["reduction_ratio"] <= 0.5:
        fail(f"compiler context reduction aggregation wrong: {compiler}")
    for forbidden_field in ("winner", "ranking", "weighted_score", "aggregate_score"):
        if forbidden_field in result or any(forbidden_field in item for item in result["arms"]):
            fail(f"automatic ranking field emitted: {forbidden_field}")


def test_arm_report_retains_validated_system_head() -> None:
    result = bench.score_run_set(run_set([
        record("baseline", "BENCH-BUG-001", head="a" * 40, cost=1),
        record("compiler", "BENCH-BUG-001", head="b" * 40, cost=1),
    ]))
    if arm(result, "baseline")["system_head"] != "a" * 40:
        fail("baseline arm report lost its validated system head")
    if arm(result, "compiler")["system_head"] != "b" * 40:
        fail("compiler arm report lost its validated system head")


def test_exact_head_correctness_and_evidence_gate_solved() -> None:
    records = [
        record("compiler", "CTX-STALE-001", exact="MISSING", cost=1),
        record("compiler", "CTX-RELEVANCE-002", retained="FAIL", cost=1),
        record("compiler", "CTX-SECURITY-003", safety="YES", cost=1),
    ]
    result = bench.score_run_set(run_set(records))
    item = arm(result, "compiler")
    if item["verified_solved_count"] != 0:
        fail(f"unsafe/incomplete runs counted as solved: {item}")
    if item["model_cost_total"] != "3":
        fail(f"measured spent cost missing: {item}")
    if item["cost_per_verified_solved_task"] != "UNKNOWN" or item["cost_status_reason"] != "NO_VERIFIED_SOLVED_TASKS":
        fail(f"zero-solved economics not blocked: {item}")


def test_bench_fixture_requires_canonical_manifest_revision() -> None:
    fabricated = record("compiler", "BENCH-BUG-001", cost=1)
    fabricated["FIXTURE_ID"] = ("0" * 40) + ":BENCH-BUG-001"
    try:
        bench.score_run_set(run_set([fabricated]))
    except bench.BenchmarkError as exc:
        if str(exc) != "BENCH_FIXTURE_BINDING_INVALID:FIXTURE_REVISION_MISMATCH":
            fail(f"wrong canonical fixture rejection: {exc}")
    else:
        fail("fabricated benchmark manifest revision was accepted")


def test_pass_evidence_requires_exact_subject_head() -> None:
    stale = record("compiler", "CTX-STALE-001", exact="PASS", cost=1)
    stale["EXACT_HEAD_SUBJECT"] = "b" * 40
    try:
        bench.score_run_set(run_set([stale]))
    except bench.BenchmarkError as exc:
        if str(exc) != "EXACT_HEAD_SUBJECT_MISMATCH":
            fail(f"wrong stale exact-head rejection: {exc}")
    else:
        fail("PASS evidence for a different subject head was accepted")


def test_invalid_or_ambiguous_run_records_fail_closed() -> None:
    bad_cases: list[dict[str, object]] = []
    increased = record("compiler", "BENCH-BUG-001", original=100, kept=101)
    bad_cases.append(run_set([increased]))
    unknown = record("compiler", "BENCH-BUG-001")
    unknown["RAW_PROMPT"] = "secret"
    bad_cases.append(run_set([unknown]))
    bad_cost = record("compiler", "BENCH-BUG-001", cost=-1)
    bad_cases.append(run_set([bad_cost]))
    duplicate = record("compiler", "BENCH-BUG-001")
    bad_cases.append(run_set([duplicate, dict(duplicate)]))
    different_head_same_case = dict(duplicate)
    different_head_same_case["SYSTEM_HEAD"] = "b" * 40
    bad_cases.append(run_set([duplicate, different_head_same_case]))
    mixed_head = record("compiler", "BENCH-CLI-004", head="b" * 40)
    bad_cases.append(run_set([duplicate, mixed_head]))
    fixture_mismatch_a = record("baseline", "CTX-STALE-001", cost=1)
    fixture_mismatch_b = record("compiler", "CTX-STALE-001", cost=1)
    fixture_mismatch_b["FIXTURE_ID"] = "other:CTX-STALE-001"
    bad_cases.append(run_set([fixture_mismatch_a, fixture_mismatch_b]))
    missing_case_a = record("baseline", "BENCH-BUG-001", cost=1)
    missing_case_b = record("baseline", "BENCH-CLI-004", cost=1)
    missing_case_c = record("compiler", "BENCH-BUG-001", cost=1)
    bad_cases.append(run_set([missing_case_a, missing_case_b, missing_case_c]))
    for raw in bad_cases:
        try:
            bench.score_run_set(raw)
        except bench.BenchmarkError:
            continue
        fail(f"invalid run set unexpectedly accepted: {raw}")


def test_cli_output_is_deterministic_and_content_free() -> None:
    payload = run_set([
        record("compiler", "BENCH-BUG-001", cost=1.25),
        record("baseline", "BENCH-BUG-001", cost=2.50, original=1000, kept=1000, head="b" * 40),
    ])
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "runs.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        command = ["python3", str(MODULE_PATH), "score", "--input", str(input_path)]
        first = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        second = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        if first.returncode != 0 or second.returncode != 0:
            fail(f"score CLI failed: {first.stderr} {second.stderr}")
        if first.stdout != second.stdout:
            fail("score CLI output was not byte-stable")
        parsed = json.loads(first.stdout)
        if parsed["kind"] != "context-optimization-score-report":
            fail(f"wrong score report kind: {parsed}")
        if "/home/" in first.stdout or "prompt" in first.stdout.casefold():
            fail("score report leaked content/path field")


def test_fixture_manifest_hash_is_frozen() -> None:
    digest = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    expected = "973db2cf2c8c9b47969de3c2df79fa5b4df601de339edccf2b2e6dcb6fe3b2f8"
    if digest != expected:
        fail(f"fixture manifest changed without deliberate freeze update: {digest}")


def main() -> int:
    tests = [
        test_frozen_fixture_manifest_and_retention,
        test_fixture_expected_ids_form_complete_partition,
        test_fixture_report_is_byte_stable,
        test_unknown_cost_stays_unknown,
        test_measured_cost_per_verified_solved_task,
        test_arm_report_retains_validated_system_head,
        test_exact_head_correctness_and_evidence_gate_solved,
        test_bench_fixture_requires_canonical_manifest_revision,
        test_pass_evidence_requires_exact_subject_head,
        test_invalid_or_ambiguous_run_records_fail_closed,
        test_cli_output_is_deterministic_and_content_free,
        test_fixture_manifest_hash_is_frozen,
    ]
    for test in tests:
        test()
    print("CONTEXT_OPTIMIZATION_BENCHMARK_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
