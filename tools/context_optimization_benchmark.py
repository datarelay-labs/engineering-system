#!/usr/bin/env python3
"""Deterministic context-optimization fixture evaluator and factual scorer.

This helper never launches workers, calls models/providers, estimates provider
cost, or ranks a winning arm.  It measures retention/volume on frozen offline
fixtures and aggregates caller-supplied verified run facts.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import benchmark_execution
import benchmark_fixture

CONTEXT_COMPILER_PATH = TOOLS / "context_compiler.py"
DEFAULT_FIXTURE_PATH = ROOT / "evals/context-optimization/fixtures.json"
ARM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MANIFEST_CASE_RE = re.compile(r"^CTX-[A-Z0-9-]{1,72}$")
RUN_CASE_RE = re.compile(r"^(?:CTX|BENCH)-[A-Z0-9-]{1,72}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FIXTURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
MAX_CASES = 32
MAX_RECORDS = 256


spec = importlib.util.spec_from_file_location("context_compiler_for_benchmark", CONTEXT_COMPILER_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("context compiler import unavailable")
context_compiler = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = context_compiler
spec.loader.exec_module(context_compiler)


class BenchmarkError(ValueError):
    pass


RUN_FIELDS = frozenset({
    "ARM_ID",
    "CASE_ID",
    "SYSTEM_HEAD",
    "FIXTURE_ID",
    "TERMINAL",
    "CORRECT_BEHAVIOR",
    "SAFETY_REGRESSION",
    "EXACT_HEAD_EVIDENCE",
    "EXACT_HEAD_SUBJECT",
    "REQUIRED_EVIDENCE_RETAINED",
    "ORIGINAL_CONTEXT_BYTES",
    "KEPT_CONTEXT_BYTES",
    "MODEL_COST",
    "RETRIES",
    "REVIEW_REWORK",
    "HUMAN_INTERVENTIONS",
})
RUN_SET_FIELDS = frozenset({"schema_version", "kind", "records"})
MANIFEST_FIELDS = frozenset({"schema_version", "kind", "cases"})
CASE_FIELDS = frozenset({
    "id",
    "task",
    "budget_bytes",
    "blocks",
    "required_protected_ids",
    "required_optional_kept_ids",
    "required_dropped_ids",
    "required_references",
})


def _unknown_keys(value: dict[str, Any], allowed: frozenset[str], code: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise BenchmarkError(code + ":" + ",".join(unknown))


def _require_str(value: Any, code: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise BenchmarkError(code)
    if pattern is not None and pattern.fullmatch(value) is None:
        raise BenchmarkError(code)
    return value


def _require_nonnegative_int(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BenchmarkError(code)
    return value


def _require_id_list(value: Any, code: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise BenchmarkError(code)
    result: list[str] = []
    for item in value:
        result.append(_require_str(item, code, context_compiler.SAFE_ID_RE))
    if len(result) != len(set(result)):
        raise BenchmarkError(code)
    return result


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError("JSON_INVALID") from exc


def validate_manifest(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BenchmarkError("MANIFEST_INVALID")
    _unknown_keys(raw, MANIFEST_FIELDS, "MANIFEST_UNKNOWN_FIELD")
    if raw.get("schema_version") != 1 or raw.get("kind") != "context-optimization-fixture-manifest":
        raise BenchmarkError("MANIFEST_IDENTITY_INVALID")
    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases or len(cases) > MAX_CASES:
        raise BenchmarkError("MANIFEST_CASES_INVALID")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            raise BenchmarkError("CASE_INVALID")
        _unknown_keys(case, CASE_FIELDS, "CASE_UNKNOWN_FIELD")
        case_id = _require_str(case.get("id"), "CASE_ID_INVALID", MANIFEST_CASE_RE)
        if case_id in seen:
            raise BenchmarkError("CASE_ID_DUPLICATE")
        seen.add(case_id)
        task = _require_str(case.get("task"), "CASE_TASK_INVALID")
        budget = _require_nonnegative_int(case.get("budget_bytes"), "CASE_BUDGET_INVALID")
        if budget <= 0:
            raise BenchmarkError("CASE_BUDGET_INVALID")
        blocks = case.get("blocks")
        try:
            parsed_task, parsed_blocks = context_compiler.parse_request({"task": task, "blocks": blocks})
        except context_compiler.CompilerError as exc:
            raise BenchmarkError(f"CASE_COMPILER_INPUT_INVALID:{case_id}:{exc}") from exc
        protected_ids = _require_id_list(case.get("required_protected_ids"), "CASE_PROTECTED_IDS_INVALID")
        kept_ids = _require_id_list(case.get("required_optional_kept_ids"), "CASE_KEPT_IDS_INVALID")
        dropped_ids = _require_id_list(case.get("required_dropped_ids"), "CASE_DROPPED_IDS_INVALID")
        all_ids = {item.block_id for item in parsed_blocks}
        if not (set(protected_ids) | set(kept_ids) | set(dropped_ids)) <= all_ids:
            raise BenchmarkError("CASE_EXPECTED_ID_MISSING")
        if (set(protected_ids) & set(kept_ids)) or (set(protected_ids) & set(dropped_ids)) or (set(kept_ids) & set(dropped_ids)):
            raise BenchmarkError("CASE_EXPECTED_ID_OVERLAP")
        expected_ids = set(protected_ids) | set(kept_ids) | set(dropped_ids)
        if expected_ids != all_ids:
            raise BenchmarkError("CASE_EXPECTED_ID_PARTITION_INVALID")
        actual_protected = {item.block_id for item in parsed_blocks if item.protected}
        if set(protected_ids) != actual_protected:
            raise BenchmarkError("CASE_PROTECTED_IDS_MISMATCH")
        references = case.get("required_references", {})
        if not isinstance(references, dict):
            raise BenchmarkError("CASE_REFERENCES_INVALID")
        block_by_id = {item.block_id: item for item in parsed_blocks}
        for block_id, reference in references.items():
            _require_str(block_id, "CASE_REFERENCE_ID_INVALID", context_compiler.SAFE_ID_RE)
            expected = _require_str(reference, "CASE_REFERENCE_INVALID")
            block = block_by_id.get(block_id)
            if block is None or block.reference != expected:
                raise BenchmarkError("CASE_REFERENCE_MISMATCH")
        normalized.append({
            "id": case_id,
            "task": parsed_task,
            "budget_bytes": budget,
            "blocks": parsed_blocks,
            "required_protected_ids": protected_ids,
            "required_optional_kept_ids": kept_ids,
            "required_dropped_ids": dropped_ids,
            "required_references": dict(sorted(references.items())),
        })
    return {"schema_version": 1, "kind": raw["kind"], "cases": normalized}


def evaluate_manifest(raw: Any) -> dict[str, Any]:
    manifest = validate_manifest(raw)
    case_reports: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        result = context_compiler.compile_context(case["task"], case["blocks"], case["budget_bytes"])
        selected = [item["id"] for item in result["selected_blocks"]]
        selected_set = set(selected)
        required_selected = set(case["required_protected_ids"]) | set(case["required_optional_kept_ids"])
        if not required_selected <= selected_set:
            raise BenchmarkError(f"FIXTURE_REQUIRED_CONTEXT_MISSING:{case['id']}")
        if selected_set & set(case["required_dropped_ids"]):
            raise BenchmarkError(f"FIXTURE_REQUIRED_DROP_KEPT:{case['id']}")
        selected_blocks = {item.block_id: item for item in case["blocks"] if item.block_id in selected_set}
        for block_id in case["required_protected_ids"]:
            if not selected_blocks[block_id].protected:
                raise BenchmarkError(f"FIXTURE_PROTECTION_LOST:{case['id']}:{block_id}")
        result_metadata = {item["id"]: item for item in result["selected_blocks"]}
        for block_id, expected_reference in case["required_references"].items():
            if block_id not in result_metadata or result_metadata[block_id].get("reference") != expected_reference:
                raise BenchmarkError(f"FIXTURE_REFERENCE_LOST:{case['id']}:{block_id}")
        telemetry = result["telemetry"]
        case_reports.append({
            "case_id": case["id"],
            "decision": "PASS",
            "budget_bytes": case["budget_bytes"],
            "original_bytes": telemetry["original_bytes"],
            "kept_bytes": telemetry["kept_bytes"],
            "reduction_ratio": telemetry["reduction_ratio"],
            "protected_block_count": telemetry["protected_block_count"],
            "optional_kept_count": telemetry["optional_kept_count"],
            "optional_dropped_count": telemetry["optional_dropped_count"],
            "selected_ids": selected,
        })
    return {
        "schema_version": 1,
        "kind": "context-optimization-fixture-report",
        "decision": "PASS",
        "case_count": len(case_reports),
        "cases": case_reports,
    }


def _parse_model_cost(value: Any) -> Decimal | None:
    if value == "UNKNOWN":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkError("MODEL_COST_INVALID")
    try:
        cost = Decimal(str(value))
    except InvalidOperation as exc:
        raise BenchmarkError("MODEL_COST_INVALID") from exc
    if not cost.is_finite() or cost < 0:
        raise BenchmarkError("MODEL_COST_INVALID")
    return cost


@lru_cache(maxsize=1)
def _canonical_benchmark_manifest() -> dict[str, Any]:
    try:
        manifest = benchmark_fixture.load_manifest()
        benchmark_execution._require_frozen_manifest(manifest)
    except (benchmark_fixture.FixtureError, benchmark_execution.ExecutionError) as exc:
        code = getattr(exc, "code", str(exc))
        raise BenchmarkError(f"BENCHMARK_MANIFEST_INVALID:{code}") from exc
    return manifest


def _bind_benchmark_fixture(case_id: str, fixture_id: str) -> None:
    try:
        bound = benchmark_fixture.bind_fixture_id(
            fixture_id,
            _canonical_benchmark_manifest(),
            benchmark_execution.PILOT_MANIFEST_HEAD,
        )
    except benchmark_fixture.FixtureError as exc:
        raise BenchmarkError(f"BENCH_FIXTURE_BINDING_INVALID:{exc.code}") from exc
    if bound["case_id"] != case_id:
        raise BenchmarkError("BENCH_FIXTURE_CASE_MISMATCH")


def _validate_record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BenchmarkError("RUN_RECORD_INVALID")
    _unknown_keys(raw, RUN_FIELDS, "RUN_RECORD_UNKNOWN_FIELD")
    if set(raw) != RUN_FIELDS:
        missing = sorted(RUN_FIELDS - set(raw))
        raise BenchmarkError("RUN_RECORD_MISSING_FIELD:" + ",".join(missing))
    arm = _require_str(raw["ARM_ID"], "ARM_ID_INVALID", ARM_RE)
    case_id = _require_str(raw["CASE_ID"], "CASE_ID_INVALID", RUN_CASE_RE)
    head = _require_str(raw["SYSTEM_HEAD"], "SYSTEM_HEAD_INVALID", SHA_RE)
    fixture_id = _require_str(raw["FIXTURE_ID"], "FIXTURE_ID_INVALID", FIXTURE_ID_RE)
    if case_id.startswith("BENCH-"):
        _bind_benchmark_fixture(case_id, fixture_id)
    terminal = raw["TERMINAL"]
    correct = raw["CORRECT_BEHAVIOR"]
    safety = raw["SAFETY_REGRESSION"]
    exact = raw["EXACT_HEAD_EVIDENCE"]
    exact_subject = raw["EXACT_HEAD_SUBJECT"]
    retained = raw["REQUIRED_EVIDENCE_RETAINED"]
    if terminal not in {"PASS", "BLOCK", "FAIL"}:
        raise BenchmarkError("TERMINAL_INVALID")
    if correct not in {"PASS", "FAIL"}:
        raise BenchmarkError("CORRECT_BEHAVIOR_INVALID")
    if safety not in {"YES", "NO"}:
        raise BenchmarkError("SAFETY_REGRESSION_INVALID")
    if exact not in {"PASS", "FAIL", "MISSING"}:
        raise BenchmarkError("EXACT_HEAD_EVIDENCE_INVALID")
    if exact_subject != "MISSING":
        _require_str(exact_subject, "EXACT_HEAD_SUBJECT_INVALID", SHA_RE)
    if exact == "PASS" and exact_subject != head:
        raise BenchmarkError("EXACT_HEAD_SUBJECT_MISMATCH")
    if exact == "MISSING" and exact_subject != "MISSING":
        raise BenchmarkError("EXACT_HEAD_SUBJECT_INVALID")
    if retained not in {"PASS", "FAIL", "MISSING"}:
        raise BenchmarkError("REQUIRED_EVIDENCE_RETAINED_INVALID")
    original = _require_nonnegative_int(raw["ORIGINAL_CONTEXT_BYTES"], "ORIGINAL_CONTEXT_BYTES_INVALID")
    kept = _require_nonnegative_int(raw["KEPT_CONTEXT_BYTES"], "KEPT_CONTEXT_BYTES_INVALID")
    if kept > original:
        raise BenchmarkError("CONTEXT_BYTES_INCREASED")
    retries = _require_nonnegative_int(raw["RETRIES"], "RETRIES_INVALID")
    rework = _require_nonnegative_int(raw["REVIEW_REWORK"], "REVIEW_REWORK_INVALID")
    human = _require_nonnegative_int(raw["HUMAN_INTERVENTIONS"], "HUMAN_INTERVENTIONS_INVALID")
    cost = _parse_model_cost(raw["MODEL_COST"])
    solved = terminal == "PASS" and correct == "PASS" and safety == "NO" and exact == "PASS" and retained == "PASS"
    return {
        "ARM_ID": arm,
        "CASE_ID": case_id,
        "SYSTEM_HEAD": head,
        "FIXTURE_ID": fixture_id,
        "VERIFIED_SOLVED": solved,
        "ORIGINAL_CONTEXT_BYTES": original,
        "KEPT_CONTEXT_BYTES": kept,
        "MODEL_COST_DECIMAL": cost,
        "RETRIES": retries,
        "REVIEW_REWORK": rework,
        "HUMAN_INTERVENTIONS": human,
    }


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    return "0" if text in {"-0", ""} else text


def score_run_set(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BenchmarkError("RUN_SET_INVALID")
    _unknown_keys(raw, RUN_SET_FIELDS, "RUN_SET_UNKNOWN_FIELD")
    if raw.get("schema_version") != 1 or raw.get("kind") != "context-optimization-run-set":
        raise BenchmarkError("RUN_SET_IDENTITY_INVALID")
    records_raw = raw.get("records")
    if not isinstance(records_raw, list) or not records_raw or len(records_raw) > MAX_RECORDS:
        raise BenchmarkError("RUN_SET_RECORDS_INVALID")
    records = [_validate_record(item) for item in records_raw]
    identities: set[tuple[str, str]] = set()
    case_fixtures: dict[str, str] = {}
    arm_cases: dict[str, set[str]] = {}
    for record in records:
        identity = (record["ARM_ID"], record["CASE_ID"])
        if identity in identities:
            raise BenchmarkError("RUN_RECORD_DUPLICATE")
        identities.add(identity)
        prior_fixture = case_fixtures.setdefault(record["CASE_ID"], record["FIXTURE_ID"])
        if prior_fixture != record["FIXTURE_ID"]:
            raise BenchmarkError("CASE_FIXTURE_MISMATCH")
        arm_cases.setdefault(record["ARM_ID"], set()).add(record["CASE_ID"])
    expected_cases = next(iter(arm_cases.values()))
    if any(cases != expected_cases for cases in arm_cases.values()):
        raise BenchmarkError("ARM_CASE_SET_MISMATCH")

    arm_reports: list[dict[str, Any]] = []
    for arm in sorted(arm_cases):
        items = [record for record in records if record["ARM_ID"] == arm]
        heads = {item["SYSTEM_HEAD"] for item in items}
        if len(heads) != 1:
            raise BenchmarkError("ARM_SYSTEM_HEAD_MISMATCH")
        original = sum(item["ORIGINAL_CONTEXT_BYTES"] for item in items)
        kept = sum(item["KEPT_CONTEXT_BYTES"] for item in items)
        solved = sum(1 for item in items if item["VERIFIED_SOLVED"])
        costs = [item["MODEL_COST_DECIMAL"] for item in items]
        if any(cost is None for cost in costs):
            cost_total: str = "UNKNOWN"
            cost_per_solved: str = "UNKNOWN"
            cost_reason = "MODEL_COST_INCOMPLETE"
        else:
            measured_total = sum((cost for cost in costs if cost is not None), Decimal("0"))
            cost_total = _decimal_text(measured_total)
            if solved == 0:
                cost_per_solved = "UNKNOWN"
                cost_reason = "NO_VERIFIED_SOLVED_TASKS"
            else:
                cost_per_solved = _decimal_text(measured_total / Decimal(solved))
                cost_reason = "MEASURED"
        reduction = 0.0 if original == 0 else round(1.0 - (kept / original), 6)
        arm_reports.append({
            "arm_id": arm,
            "run_count": len(items),
            "verified_solved_count": solved,
            "original_context_bytes": original,
            "kept_context_bytes": kept,
            "reduction_ratio": reduction,
            "retries_total": sum(item["RETRIES"] for item in items),
            "review_rework_total": sum(item["REVIEW_REWORK"] for item in items),
            "human_interventions_total": sum(item["HUMAN_INTERVENTIONS"] for item in items),
            "model_cost_total": cost_total,
            "cost_per_verified_solved_task": cost_per_solved,
            "cost_status_reason": cost_reason,
        })
    return {
        "schema_version": 1,
        "kind": "context-optimization-score-report",
        "record_count": len(records),
        "arm_count": len(arm_reports),
        "arms": arm_reports,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure context optimization without inventing economics")
    sub = parser.add_subparsers(dest="command", required=True)
    fixtures = sub.add_parser("fixtures")
    fixtures.add_argument("--manifest", default=str(DEFAULT_FIXTURE_PATH))
    score = sub.add_parser("score")
    score.add_argument("--input", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "fixtures":
            result = evaluate_manifest(_load_json(Path(args.manifest)))
        elif args.command == "score":
            result = score_run_set(_load_json(Path(args.input)))
        else:
            return 2
        json.dump(result, sys.stdout, sort_keys=True, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    except BenchmarkError as exc:
        print(f"CONTEXT_OPTIMIZATION_BENCHMARK_ERROR={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
