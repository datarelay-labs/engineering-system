#!/usr/bin/env python3
"""Fail-closed live canary comparability gate for context optimizers."""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import context_optimization_benchmark as benchmark
import efficiency_telemetry as telemetry

SCHEMA_PATH = ROOT / "schemas/context-canary-comparison.schema.json"
USAGE_FIELDS = telemetry.USAGE_FIELDS
PROFILE_FIELDS = telemetry.PROFILE_FIELDS


class CanaryError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise CanaryError("INPUT_INVALID") from exc


def _validate_envelope(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CanaryError("ENVELOPE_INVALID")
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CanaryError("SCHEMA_UNAVAILABLE") from exc
    if any(True for _ in Draft202012Validator(schema).iter_errors(raw)):
        raise CanaryError("ENVELOPE_INVALID")
    return raw


def _decimal(value: Any, code: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CanaryError(code)
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise CanaryError(code) from exc
    if not parsed.is_finite() or parsed < 0:
        raise CanaryError(code)
    return parsed


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    return "0" if text in {"", "-0"} else text


def _identity(arm_id: Any, case_id: Any) -> tuple[str, str]:
    if not isinstance(arm_id, str) or benchmark.ARM_RE.fullmatch(arm_id) is None:
        raise CanaryError("ARM_ID_INVALID")
    if not isinstance(case_id, str) or benchmark.RUN_CASE_RE.fullmatch(case_id) is None:
        raise CanaryError("CASE_ID_INVALID")
    return arm_id, case_id


def _profile_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    profile = record["profile"]
    values: list[str] = []
    for field in PROFILE_FIELDS:
        value = profile[field]
        if not isinstance(value, str) or not value:
            raise CanaryError("PROFILE_UNKNOWN")
        values.append(value)
    if record["profile_switches"]:
        raise CanaryError("PROFILE_SWITCHED")
    return tuple(values)  # type: ignore[return-value]


def _usage(record: dict[str, Any]) -> dict[str, int | float]:
    usage = record["usage"]
    result: dict[str, int | float] = {}
    for field in USAGE_FIELDS:
        value = usage[field]
        if value is None:
            raise CanaryError("USAGE_INCOMPLETE")
        if field == "cost":
            _decimal(value, "USAGE_COST_INVALID")
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CanaryError("USAGE_INVALID")
        result[field] = value
    return result


def _validate_telemetry(raw: Any) -> dict[str, Any]:
    try:
        record = telemetry.parse_document(raw)
    except telemetry.TelemetryError as exc:
        raise CanaryError(f"TELEMETRY_INVALID:{exc.code}") from exc
    if record.get("kind") != "efficiency-telemetry":
        raise CanaryError("TELEMETRY_KIND_INVALID")
    validation = record["validation"]
    if (
        validation["evidence_state"] != "EXACT_HEAD"
        or validation["outcome"] != "PASS"
        or not validation["exact_head"]
    ):
        raise CanaryError("TELEMETRY_EXACT_HEAD_REQUIRED")
    _profile_key(record)
    _usage(record)
    return record


def evaluate_comparison(raw: Any) -> dict[str, Any]:
    envelope = _validate_envelope(raw)
    run_set = envelope["run_set"]
    try:
        score = benchmark.score_run_set(run_set)
    except benchmark.BenchmarkError as exc:
        raise CanaryError(f"BENCHMARK_INVALID:{exc}") from exc

    if score["arm_count"] < 2:
        raise CanaryError("MULTI_ARM_REQUIRED")

    records_raw = run_set["records"]
    expected: dict[tuple[str, str], dict[str, Any]] = {}
    system_heads: set[str] = set()
    for run in records_raw:
        identity = _identity(run["ARM_ID"], run["CASE_ID"])
        if identity in expected:
            raise CanaryError("RUN_RECORD_DUPLICATE")
        expected[identity] = run
        system_heads.add(run["SYSTEM_HEAD"])
    if len(system_heads) != 1:
        raise CanaryError("SYSTEM_HEAD_MISMATCH")
    system_head = next(iter(system_heads))

    bindings: dict[tuple[str, str], dict[str, Any]] = {}
    telemetry_run_ids: set[str] = set()
    for item in envelope["telemetry_bindings"]:
        identity = _identity(item["arm_id"], item["case_id"])
        if identity in bindings:
            raise CanaryError("TELEMETRY_BINDING_DUPLICATE")
        record = _validate_telemetry(item["telemetry"])
        run_id = record["run_id"]
        if run_id in telemetry_run_ids:
            raise CanaryError("TELEMETRY_RUN_ID_DUPLICATE")
        telemetry_run_ids.add(run_id)
        bindings[identity] = record
    if set(bindings) != set(expected):
        missing = set(expected) - set(bindings)
        extra = set(bindings) - set(expected)
        if missing:
            raise CanaryError("TELEMETRY_BINDING_MISSING")
        if extra:
            raise CanaryError("TELEMETRY_BINDING_UNKNOWN")

    profile_keys: set[tuple[str, str, str, str]] = set()
    repos: set[str] = set()
    task_kinds: set[str] = set()
    for identity, run in expected.items():
        record = bindings[identity]
        profile_keys.add(_profile_key(record))
        repos.add(record["repo"])
        task_kinds.add(record["task_kind"])
        if record["validation"]["exact_head"] != run["SYSTEM_HEAD"]:
            raise CanaryError("TELEMETRY_HEAD_MISMATCH")
        if record["terminal"] != run["TERMINAL"]:
            raise CanaryError("TERMINAL_MISMATCH")
        usage = _usage(record)
        run_cost = _decimal(run["MODEL_COST"], "MODEL_COST_REQUIRED")
        telemetry_cost = _decimal(usage["cost"], "USAGE_COST_INVALID")
        if run_cost != telemetry_cost:
            raise CanaryError("MODEL_COST_MISMATCH")
        counts = record["counts"]
        if counts["retries"] != run["RETRIES"]:
            raise CanaryError("RETRIES_MISMATCH")
        if telemetry.rework_count(counts) != run["REVIEW_REWORK"]:
            raise CanaryError("REVIEW_REWORK_MISMATCH")
        if counts["human_interventions"] != run["HUMAN_INTERVENTIONS"]:
            raise CanaryError("HUMAN_INTERVENTIONS_MISMATCH")

    if len(profile_keys) != 1:
        raise CanaryError("PROFILE_MISMATCH")
    if len(repos) != 1:
        raise CanaryError("REPO_MISMATCH")
    if len(task_kinds) != 1:
        raise CanaryError("TASK_KIND_MISMATCH")

    score_by_arm = {item["arm_id"]: item for item in score["arms"]}
    for item in score["arms"]:
        if item["verified_solved_count"] != item["run_count"]:
            raise CanaryError("VERIFIED_OUTCOME_INCOMPLETE")
        if item["cost_status_reason"] != "MEASURED":
            raise CanaryError("ECONOMICS_INCOMPLETE")

    profile_values = next(iter(profile_keys))
    profile = dict(zip(PROFILE_FIELDS, profile_values))
    arm_reports: list[dict[str, Any]] = []
    for arm_id in sorted(score_by_arm):
        score_item = score_by_arm[arm_id]
        arm_identities = sorted(identity for identity in expected if identity[0] == arm_id)
        telemetry_records = [bindings[identity] for identity in arm_identities]
        usage_totals = {
            "input_tokens": sum(record["usage"]["input_tokens"] for record in telemetry_records),
            "output_tokens": sum(record["usage"]["output_tokens"] for record in telemetry_records),
            "cache_read_tokens": sum(record["usage"]["cache_read_tokens"] for record in telemetry_records),
            "cache_write_tokens": sum(record["usage"]["cache_write_tokens"] for record in telemetry_records),
        }
        cost_total = sum(
            (_decimal(record["usage"]["cost"], "USAGE_COST_INVALID") for record in telemetry_records),
            Decimal("0"),
        )
        counts = {
            field: sum(record["counts"][field] for record in telemetry_records)
            for field in telemetry.COUNT_FIELDS
        }
        rework_total = sum(
            telemetry.rework_count(record["counts"]) for record in telemetry_records
        )
        arm_reports.append(
            {
                "arm_id": arm_id,
                "system_head": system_head,
                "run_count": score_item["run_count"],
                "verified_solved_count": score_item["verified_solved_count"],
                "original_context_bytes": score_item["original_context_bytes"],
                "kept_context_bytes": score_item["kept_context_bytes"],
                "reduction_ratio": score_item["reduction_ratio"],
                "provider_cost_total": _decimal_text(cost_total),
                "cost_per_verified_solved_task": score_item[
                    "cost_per_verified_solved_task"
                ],
                "rework_total": rework_total,
                **{f"{field}_total": value for field, value in usage_totals.items()},
                **{f"{field}_total": value for field, value in counts.items()},
            }
        )

    return {
        "schema_version": 1,
        "kind": "context-canary-eligibility-report",
        "decision": "ELIGIBLE",
        "system_head": system_head,
        "profile": profile,
        "repo": next(iter(repos)),
        "task_kind": next(iter(task_kinds)),
        "record_count": len(expected),
        "arm_count": len(arm_reports),
        "arms": arm_reports,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate live context-optimizer canary comparability"
    )
    parser.add_argument("--input", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = evaluate_comparison(_load_json(Path(args.input)))
        json.dump(result, sys.stdout, sort_keys=True, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    except CanaryError as exc:
        print(f"CONTEXT_CANARY_GATE=BLOCK reason={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
