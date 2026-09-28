#!/usr/bin/env python3
"""Strict deterministic shadow action-equivalence gate for context optimizers."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import context_canary_gate as canary_gate
import context_optimization_benchmark as benchmark

SCHEMA_PATH = ROOT / "schemas/context-shadow-comparison.schema.json"


class ShadowError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ShadowError("INPUT_INVALID") from exc


def _validate_envelope(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ShadowError("ENVELOPE_INVALID")
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowError("SCHEMA_UNAVAILABLE") from exc
    if any(True for _ in Draft202012Validator(schema).iter_errors(raw)):
        raise ShadowError("ENVELOPE_INVALID")
    return raw


def _identity(arm_id: Any, case_id: Any) -> tuple[str, str]:
    if not isinstance(arm_id, str) or benchmark.ARM_RE.fullmatch(arm_id) is None:
        raise ShadowError("ARM_ID_INVALID")
    if not isinstance(case_id, str) or benchmark.RUN_CASE_RE.fullmatch(case_id) is None:
        raise ShadowError("CASE_ID_INVALID")
    return arm_id, case_id


def _trace(actions: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(actions, list) or not actions or len(actions) > 16:
        raise ShadowError("ACTION_TRACE_INVALID")
    result: list[tuple[str, str]] = []
    for index, item in enumerate(actions):
        if not isinstance(item, dict) or set(item) != {"action", "target"}:
            raise ShadowError("ACTION_TRACE_INVALID")
        action = item.get("action")
        target = item.get("target")
        if action not in {
            "ORIENT",
            "RETRIEVE",
            "EDIT",
            "TEST",
            "REVIEW",
            "EXTERNAL_WRITE",
            "WAIT",
            "BLOCK",
            "COMPLETE",
        }:
            raise ShadowError("ACTION_TRACE_INVALID")
        if target not in {
            "REPOSITORY",
            "FILE",
            "TEST",
            "PR",
            "ISSUE",
            "CI",
            "RUNTIME",
            "NONE",
        }:
            raise ShadowError("ACTION_TRACE_INVALID")
        if action == "COMPLETE" and index != len(actions) - 1:
            raise ShadowError("ACTION_AFTER_COMPLETE")
        result.append((action, target))
    if result[-1][0] != "COMPLETE":
        raise ShadowError("TERMINAL_COMPLETE_REQUIRED")
    return tuple(result)


def evaluate_shadow(raw: Any) -> dict[str, Any]:
    envelope = _validate_envelope(raw)
    try:
        canary_report = canary_gate.evaluate_comparison(envelope["canary"])
    except canary_gate.CanaryError as exc:
        raise ShadowError(f"CANARY_INELIGIBLE:{exc}") from exc
    if canary_report.get("decision") != "ELIGIBLE":
        raise ShadowError("CANARY_INELIGIBLE")

    control_arm = envelope["control_arm_id"]
    runs = envelope["canary"]["run_set"]["records"]
    expected: dict[tuple[str, str], dict[str, Any]] = {}
    arms: set[str] = set()
    case_ids: set[str] = set()
    run_ids: set[str] = set()

    for run in runs:
        identity = _identity(run["ARM_ID"], run["CASE_ID"])
        if identity in expected:
            raise ShadowError("RUN_RECORD_DUPLICATE")
        telemetry_run_id = run.get("TELEMETRY_RUN_ID")
        if (
            not isinstance(telemetry_run_id, str)
            or benchmark.TELEMETRY_RUN_ID_RE.fullmatch(telemetry_run_id) is None
        ):
            raise ShadowError("TELEMETRY_RUN_ID_REQUIRED")
        if telemetry_run_id in run_ids:
            raise ShadowError("RUN_TELEMETRY_ID_DUPLICATE")
        run_ids.add(telemetry_run_id)
        expected[identity] = run
        arms.add(run["ARM_ID"])
        case_ids.add(run["CASE_ID"])

    if control_arm not in arms:
        raise ShadowError("CONTROL_ARM_UNKNOWN")
    if len(arms) < 2:
        raise ShadowError("MULTI_ARM_REQUIRED")

    observations: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
    observation_run_ids: set[str] = set()
    for item in envelope["observations"]:
        identity = _identity(item["arm_id"], item["case_id"])
        if identity in observations:
            raise ShadowError("OBSERVATION_DUPLICATE")
        telemetry_run_id = item["telemetry_run_id"]
        if telemetry_run_id in observation_run_ids:
            raise ShadowError("OBSERVATION_TELEMETRY_ID_DUPLICATE")
        observation_run_ids.add(telemetry_run_id)
        run = expected.get(identity)
        if run is None:
            raise ShadowError("OBSERVATION_UNKNOWN")
        if telemetry_run_id != run["TELEMETRY_RUN_ID"]:
            raise ShadowError("OBSERVATION_TELEMETRY_ID_MISMATCH")
        observations[identity] = _trace(item["actions"])

    if set(observations) != set(expected):
        if set(expected) - set(observations):
            raise ShadowError("OBSERVATION_MISSING")
        raise ShadowError("OBSERVATION_UNKNOWN")

    control_cases = {
        case_id for arm_id, case_id in expected if arm_id == control_arm
    }
    if control_cases != case_ids:
        raise ShadowError("CONTROL_CASE_SET_MISMATCH")

    arm_action_counts: dict[str, int] = {}
    for arm_id in sorted(arms):
        total = 0
        for case_id in sorted(case_ids):
            identity = (arm_id, case_id)
            trace = observations[identity]
            total += len(trace)
            if arm_id != control_arm:
                control_trace = observations[(control_arm, case_id)]
                if trace != control_trace:
                    raise ShadowError(
                        f"ACTION_TRACE_DIVERGED:{arm_id}:{case_id}"
                    )
        arm_action_counts[arm_id] = total

    return {
        "schema_version": 1,
        "kind": "context-shadow-equivalence-report",
        "decision": "EQUIVALENT",
        "control_arm_id": control_arm,
        "system_head": canary_report["system_head"],
        "profile": canary_report["profile"],
        "case_count": len(case_ids),
        "arm_count": len(arms),
        "observation_count": len(observations),
        "arms": [
            {
                "arm_id": arm_id,
                "run_count": sum(
                    1 for identity in expected if identity[0] == arm_id
                ),
                "material_action_count": arm_action_counts[arm_id],
            }
            for arm_id in sorted(arms)
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate strict shadow action equivalence"
    )
    parser.add_argument("--input", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = evaluate_shadow(_load_json(Path(args.input)))
        json.dump(result, sys.stdout, sort_keys=True, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    except ShadowError as exc:
        print(f"CONTEXT_SHADOW_GATE=BLOCK reason={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
