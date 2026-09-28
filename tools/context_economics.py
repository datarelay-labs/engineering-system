#!/usr/bin/env python3
"""Factual control-relative net context economics after strict quality gates."""
from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import context_shadow_gate as shadow_gate

SCHEMA_PATH = ROOT / "schemas/context-economics-report.schema.json"
ARM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
DELTA_FIELDS = (
    "original_context_bytes",
    "kept_context_bytes",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "tool_turns",
    "retries",
    "rereads",
    "compactions",
    "pr_rework",
    "ci_rework",
    "review_rework",
    "rework",
    "human_interventions",
)


class EconomicsError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise EconomicsError("INPUT_INVALID") from exc


def _validate_input(raw: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version", "kind", "expected_control_arm_id", "shadow"
    }:
        raise EconomicsError("ENVELOPE_INVALID")
    if raw.get("schema_version") != 1 or raw.get("kind") != "context-economics-comparison":
        raise EconomicsError("ENVELOPE_INVALID")
    control = raw.get("expected_control_arm_id")
    if not isinstance(control, str) or ARM_RE.fullmatch(control) is None:
        raise EconomicsError("CONTROL_ARM_INVALID")
    shadow = raw.get("shadow")
    if not isinstance(shadow, dict):
        raise EconomicsError("SHADOW_INVALID")
    return control, shadow


def _decimal(value: Any, code: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise EconomicsError(code)
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise EconomicsError(code) from exc
    if not parsed.is_finite() or parsed < 0:
        raise EconomicsError(code)
    return parsed


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    return "0" if text in {"", "-0"} else text


def _percent_delta(candidate: Decimal, control: Decimal) -> str | None:
    if control == 0:
        return None
    return _decimal_text(((candidate - control) / control) * Decimal("100"))


def _arm_metrics(arm: dict[str, Any]) -> dict[str, int]:
    mapping = {
        "original_context_bytes": "original_context_bytes",
        "kept_context_bytes": "kept_context_bytes",
        "input_tokens": "input_tokens_total",
        "output_tokens": "output_tokens_total",
        "cache_read_tokens": "cache_read_tokens_total",
        "cache_write_tokens": "cache_write_tokens_total",
        "tool_turns": "tool_turns_total",
        "retries": "retries_total",
        "rereads": "rereads_total",
        "compactions": "compactions_total",
        "pr_rework": "pr_rework_total",
        "ci_rework": "ci_rework_total",
        "review_rework": "review_rework_total",
        "rework": "rework_total",
        "human_interventions": "human_interventions_total",
    }
    result: dict[str, int] = {}
    for name, source in mapping.items():
        value = arm.get(source)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise EconomicsError(f"ARM_METRIC_INVALID:{source}")
        result[name] = value
    return result


def _validate_output(report: dict[str, Any]) -> dict[str, Any]:
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EconomicsError("SCHEMA_UNAVAILABLE") from exc
    if any(True for _ in Draft202012Validator(schema).iter_errors(report)):
        raise EconomicsError("REPORT_SCHEMA_INVALID")
    return report


def evaluate_economics(raw: Any) -> dict[str, Any]:
    expected_control, shadow_input = _validate_input(raw)
    try:
        shadow_report = shadow_gate.evaluate_shadow(shadow_input)
    except shadow_gate.ShadowError as exc:
        raise EconomicsError(f"SHADOW_INELIGIBLE:{exc}") from exc

    if shadow_report.get("decision") != "EQUIVALENT":
        raise EconomicsError("SHADOW_INELIGIBLE")
    if shadow_report.get("control_arm_id") != expected_control:
        raise EconomicsError("CONTROL_ARM_MISMATCH")

    try:
        canary_report = shadow_gate.canary_gate.evaluate_comparison(shadow_input["canary"])
    except shadow_gate.canary_gate.CanaryError as exc:
        raise EconomicsError(f"CANARY_INELIGIBLE:{exc}") from exc
    if canary_report.get("run_set_digest") != shadow_report.get("run_set_digest"):
        raise EconomicsError("RUN_SET_BINDING_MISMATCH")

    arms = {item["arm_id"]: item for item in canary_report["arms"]}
    control = arms.get(expected_control)
    if control is None:
        raise EconomicsError("CONTROL_ARM_UNKNOWN")
    control_metrics = _arm_metrics(control)
    control_cost = _decimal(control["provider_cost_total"], "CONTROL_COST_INVALID")

    candidates: list[dict[str, Any]] = []
    for arm_id in sorted(arms):
        if arm_id == expected_control:
            continue
        arm = arms[arm_id]
        metrics = _arm_metrics(arm)
        candidate_cost = _decimal(arm["provider_cost_total"], "CANDIDATE_COST_INVALID")
        deltas = {
            f"{field}_delta": metrics[field] - control_metrics[field]
            for field in DELTA_FIELDS
        }
        candidates.append(
            {
                "arm_id": arm_id,
                "provider_cost_delta": _decimal_text(candidate_cost - control_cost),
                "provider_cost_delta_percent": _percent_delta(candidate_cost, control_cost),
                **deltas,
            }
        )

    if not candidates:
        raise EconomicsError("CANDIDATE_REQUIRED")

    return _validate_output(
        {
            "schema_version": 1,
            "kind": "context-net-economics-report",
            "authority": "EVIDENCE_ONLY",
            "promotion_authority": "NONE",
            "control_arm_id": expected_control,
            "system_head": canary_report["system_head"],
            "repo": canary_report["repo"],
            "task_kind": canary_report["task_kind"],
            "run_set_digest": canary_report["run_set_digest"],
            "profile": canary_report["profile"],
            "case_count": shadow_report["case_count"],
            "arm_count": shadow_report["arm_count"],
            "candidates": candidates,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report factual candidate-minus-control net context economics"
    )
    parser.add_argument("--input", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = evaluate_economics(_load_json(Path(args.input)))
        json.dump(report, sys.stdout, sort_keys=True, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    except EconomicsError as exc:
        print(f"CONTEXT_ECONOMICS=BLOCK reason={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
