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
TOOL = TOOLS / "context_shadow_gate.py"
sys.path.insert(0, str(TOOLS))

import efficiency_telemetry as telemetry

spec = importlib.util.spec_from_file_location("context_shadow_gate_tested", TOOL)
assert spec and spec.loader
shadow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = shadow
spec.loader.exec_module(shadow)

HEAD = subprocess.check_output(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
).strip()
PROFILE = {
    "provider": "provider-a",
    "model": "model-x",
    "reasoning": "medium",
    "toolset": "engineering-default",
}
RUN_IDS = {
    ("baseline", "CTX-SHADOW-001"): "1" * 32,
    ("baseline", "CTX-SHADOW-002"): "2" * 32,
    ("candidate", "CTX-SHADOW-001"): "3" * 32,
    ("candidate", "CTX-SHADOW-002"): "4" * 32,
}


def fail(message: str) -> None:
    raise AssertionError(message)


def expect_error(code: str, callback) -> None:
    try:
        callback()
    except shadow.ShadowError as exc:
        if str(exc) != code:
            fail(f"expected {code}, got {exc}")
    else:
        fail(f"expected failure {code}")


def counts(**overrides: int) -> dict[str, int]:
    result = {field: 0 for field in telemetry.COUNT_FIELDS}
    result.update(overrides)
    return result


def telemetry_record(
    run_id: str,
    *,
    cost: float = 1.0,
    profile: dict[str, object] | None = None,
) -> dict[str, object]:
    return telemetry.build_record(
        repo="datarelay-labs/engineering-system",
        workstream="context-shadow-equivalence",
        task_kind="DEVELOPMENT",
        profile=PROFILE if profile is None else profile,
        started_at="2026-09-28T06:00:00Z",
        finished_at="2026-09-28T06:00:05Z",
        duration_seconds=5,
        counts=counts(),
        validation={
            "ids": ["ENG-SHADOW-001"],
            "exact_head": HEAD,
            "evidence_state": "EXACT_HEAD",
            "outcome": "PASS",
        },
        terminal="PASS",
        budget=telemetry.TaskBudget(None, None),
        usage={
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_tokens": 40,
            "cache_write_tokens": 10,
            "cost": cost,
        },
        run_id=run_id,
        root=ROOT,
    )


def run_record(
    arm: str,
    case_id: str,
    *,
    run_id: str,
    cost: float = 1.0,
) -> dict[str, object]:
    return {
        "ARM_ID": arm,
        "CASE_ID": case_id,
        "SYSTEM_HEAD": HEAD,
        "FIXTURE_ID": f"fixture:{case_id}",
        "TERMINAL": "PASS",
        "CORRECT_BEHAVIOR": "PASS",
        "SAFETY_REGRESSION": "NO",
        "EXACT_HEAD_EVIDENCE": "PASS",
        "EXACT_HEAD_SUBJECT": HEAD,
        "REQUIRED_EVIDENCE_RETAINED": "PASS",
        "ORIGINAL_CONTEXT_BYTES": 1000,
        "KEPT_CONTEXT_BYTES": 600 if arm == "candidate" else 1000,
        "MODEL_COST": cost,
        "RETRIES": 0,
        "REVIEW_REWORK": 0,
        "HUMAN_INTERVENTIONS": 0,
        "TELEMETRY_RUN_ID": run_id,
    }


def material_trace() -> list[dict[str, str]]:
    return [
        {"action": "ORIENT", "target": "REPOSITORY"},
        {"action": "RETRIEVE", "target": "FILE"},
        {"action": "EDIT", "target": "FILE"},
        {"action": "TEST", "target": "TEST"},
        {"action": "REVIEW", "target": "PR"},
        {"action": "COMPLETE", "target": "NONE"},
    ]


def canary_document() -> dict[str, object]:
    runs: list[dict[str, object]] = []
    bindings: list[dict[str, object]] = []
    for arm in ("baseline", "candidate"):
        for case_id in ("CTX-SHADOW-001", "CTX-SHADOW-002"):
            run_id = RUN_IDS[(arm, case_id)]
            runs.append(
                run_record(
                    arm,
                    case_id,
                    run_id=run_id,
                    cost=1.0,
                )
            )
            bindings.append(
                {
                    "arm_id": arm,
                    "case_id": case_id,
                    "telemetry": telemetry_record(run_id, cost=1.0),
                }
            )
    return {
        "schema_version": 1,
        "kind": "context-canary-comparison",
        "run_set": {
            "schema_version": 1,
            "kind": "context-optimization-run-set",
            "records": runs,
        },
        "telemetry_bindings": bindings,
    }


def shadow_document() -> dict[str, object]:
    canary = canary_document()
    observations: list[dict[str, object]] = []
    for run in canary["run_set"]["records"]:
        observations.append(
            {
                "arm_id": run["ARM_ID"],
                "case_id": run["CASE_ID"],
                "telemetry_run_id": run["TELEMETRY_RUN_ID"],
                "actions": material_trace(),
            }
        )
    return {
        "schema_version": 1,
        "kind": "context-shadow-comparison",
        "control_arm_id": "baseline",
        "canary": canary,
        "observations": observations,
    }


def observation(
    doc: dict[str, object],
    arm_id: str,
    case_id: str,
) -> dict[str, object]:
    for item in doc["observations"]:
        if item["arm_id"] == arm_id and item["case_id"] == case_id:
            return item
    fail(f"observation not found: {arm_id}/{case_id}")


def test_identical_material_actions_are_equivalent() -> None:
    report = shadow.evaluate_shadow(shadow_document())
    if report["decision"] != "EQUIVALENT":
        fail(f"valid shadow comparison was not equivalent: {report}")
    if report["control_arm_id"] != "baseline":
        fail(f"control arm drifted: {report}")
    if report["system_head"] != HEAD or report["profile"] != PROFILE:
        fail(f"head/profile facts drifted: {report}")
    if (
        report["case_count"] != 2
        or report["arm_count"] != 2
        or report["observation_count"] != 4
    ):
        fail(f"report counts drifted: {report}")
    arms = {item["arm_id"]: item for item in report["arms"]}
    for arm_id in ("baseline", "candidate"):
        if arms[arm_id]["run_count"] != 2:
            fail(f"run count drifted for {arm_id}: {arms[arm_id]}")
        if arms[arm_id]["material_action_count"] != 12:
            fail(f"action count drifted for {arm_id}: {arms[arm_id]}")

    encoded = json.dumps(report, sort_keys=True)
    for forbidden in (
        "winner",
        "ranking",
        "recommendation",
        "score",
        "probability",
        "confidence",
        "telemetry_run_id",
        "ACTION_TRACE",
    ):
        if forbidden in encoded:
            fail(f"shadow report leaked judgment/raw trace field: {forbidden}")


def test_canary_and_control_fail_closed() -> None:
    doc = shadow_document()
    candidate = doc["canary"]["telemetry_bindings"][2]["telemetry"]
    candidate["profile"]["model"] = "model-y"
    expect_error(
        "CANARY_INELIGIBLE:PROFILE_MISMATCH",
        lambda: shadow.evaluate_shadow(doc),
    )

    doc = shadow_document()
    doc["control_arm_id"] = "missing-arm"
    expect_error("CONTROL_ARM_UNKNOWN", lambda: shadow.evaluate_shadow(doc))

    doc = shadow_document()
    del doc["control_arm_id"]
    expect_error("ENVELOPE_INVALID", lambda: shadow.evaluate_shadow(doc))

    doc = shadow_document()
    doc["control_arm_id"] = "Bad Arm"
    expect_error("ENVELOPE_INVALID", lambda: shadow.evaluate_shadow(doc))


def test_observation_mapping_fail_closed() -> None:
    doc = shadow_document()
    doc["observations"].pop()
    expect_error("OBSERVATION_MISSING", lambda: shadow.evaluate_shadow(doc))

    doc = shadow_document()
    doc["observations"].append(copy.deepcopy(doc["observations"][0]))
    expect_error("OBSERVATION_DUPLICATE", lambda: shadow.evaluate_shadow(doc))

    doc = shadow_document()
    extra = copy.deepcopy(doc["observations"][0])
    extra["case_id"] = "CTX-SHADOW-999"
    extra["telemetry_run_id"] = "f" * 32
    doc["observations"].append(extra)
    expect_error("OBSERVATION_UNKNOWN", lambda: shadow.evaluate_shadow(doc))


def test_telemetry_identity_fail_closed() -> None:
    doc = shadow_document()
    del doc["observations"][0]["telemetry_run_id"]
    expect_error("ENVELOPE_INVALID", lambda: shadow.evaluate_shadow(doc))

    doc = shadow_document()
    doc["observations"][0]["telemetry_run_id"] = "f" * 32
    expect_error(
        "OBSERVATION_TELEMETRY_ID_MISMATCH",
        lambda: shadow.evaluate_shadow(doc),
    )

    doc = shadow_document()
    doc["observations"][1]["telemetry_run_id"] = (
        doc["observations"][0]["telemetry_run_id"]
    )
    expect_error(
        "OBSERVATION_TELEMETRY_ID_DUPLICATE",
        lambda: shadow.evaluate_shadow(doc),
    )


def test_swapped_observation_identity_is_rejected() -> None:
    doc = shadow_document()
    baseline = observation(doc, "baseline", "CTX-SHADOW-001")
    candidate = observation(doc, "candidate", "CTX-SHADOW-001")
    baseline_id = baseline["telemetry_run_id"]
    candidate_id = candidate["telemetry_run_id"]
    baseline["telemetry_run_id"] = candidate_id
    candidate["telemetry_run_id"] = baseline_id
    expect_error(
        "OBSERVATION_TELEMETRY_ID_MISMATCH",
        lambda: shadow.evaluate_shadow(doc),
    )

    doc = shadow_document()
    del doc["canary"]["run_set"]["records"][0]["TELEMETRY_RUN_ID"]
    expect_error(
        "CANARY_INELIGIBLE:TELEMETRY_RUN_ID_REQUIRED",
        lambda: shadow.evaluate_shadow(doc),
    )


def test_action_trace_structure_fail_closed() -> None:
    doc = shadow_document()
    item = observation(doc, "candidate", "CTX-SHADOW-001")
    item["actions"] = [
        {"action": "ORIENT", "target": "REPOSITORY"}
        for _ in range(16)
    ] + [{"action": "COMPLETE", "target": "NONE"}]
    expect_error("ENVELOPE_INVALID", lambda: shadow.evaluate_shadow(doc))

    doc = shadow_document()
    item = observation(doc, "candidate", "CTX-SHADOW-001")
    item["actions"][0]["action"] = "UNKNOWN"
    expect_error("ENVELOPE_INVALID", lambda: shadow.evaluate_shadow(doc))

    doc = shadow_document()
    item = observation(doc, "candidate", "CTX-SHADOW-001")
    item["actions"][0]["target"] = "SHELL"
    expect_error("ENVELOPE_INVALID", lambda: shadow.evaluate_shadow(doc))

    doc = shadow_document()
    item = observation(doc, "candidate", "CTX-SHADOW-001")
    item["actions"].insert(1, {"action": "COMPLETE", "target": "NONE"})
    expect_error("ACTION_AFTER_COMPLETE", lambda: shadow.evaluate_shadow(doc))


def test_terminal_complete_contract_fail_closed() -> None:
    doc = shadow_document()
    item = observation(doc, "candidate", "CTX-SHADOW-001")
    item["actions"] = item["actions"][:-1]
    expect_error(
        "TERMINAL_COMPLETE_REQUIRED",
        lambda: shadow.evaluate_shadow(doc),
    )


def test_block_and_wait_actions_are_compared_not_rejected() -> None:
    doc = shadow_document()
    trace = [
        {"action": "ORIENT", "target": "REPOSITORY"},
        {"action": "BLOCK", "target": "NONE"},
        {"action": "WAIT", "target": "RUNTIME"},
        {"action": "RETRIEVE", "target": "FILE"},
        {"action": "TEST", "target": "TEST"},
        {"action": "COMPLETE", "target": "NONE"},
    ]
    for arm_id in ("baseline", "candidate"):
        item = observation(doc, arm_id, "CTX-SHADOW-001")
        item["actions"] = copy.deepcopy(trace)
    report = shadow.evaluate_shadow(doc)
    if report["decision"] != "EQUIVALENT":
        fail(f"equivalent BLOCK/WAIT material trace was rejected: {report}")


def test_content_bearing_fields_are_rejected() -> None:
    for field in ("prompt", "path", "secret", "rationale"):
        doc = shadow_document()
        item = observation(doc, "candidate", "CTX-SHADOW-001")
        item[field] = "forbidden-content"
        expect_error("ENVELOPE_INVALID", lambda doc=doc: shadow.evaluate_shadow(doc))

    doc = shadow_document()
    item = observation(doc, "candidate", "CTX-SHADOW-001")
    item["actions"][0]["command"] = "forbidden-command"
    expect_error("ENVELOPE_INVALID", lambda: shadow.evaluate_shadow(doc))

    doc = shadow_document()
    doc["raw_tool_output"] = "forbidden-output"
    expect_error("ENVELOPE_INVALID", lambda: shadow.evaluate_shadow(doc))


def test_material_action_or_target_divergence_fails_closed() -> None:
    doc = shadow_document()
    item = observation(doc, "candidate", "CTX-SHADOW-001")
    item["actions"][1]["action"] = "REVIEW"
    expect_error(
        "ACTION_TRACE_DIVERGED:candidate:CTX-SHADOW-001",
        lambda: shadow.evaluate_shadow(doc),
    )

    doc = shadow_document()
    item = observation(doc, "candidate", "CTX-SHADOW-001")
    item["actions"][1]["target"] = "REPOSITORY"
    expect_error(
        "ACTION_TRACE_DIVERGED:candidate:CTX-SHADOW-001",
        lambda: shadow.evaluate_shadow(doc),
    )


def test_cli_output_is_deterministic_and_content_free() -> None:
    doc = shadow_document()
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "shadow.json"
        source.write_text(json.dumps(doc), encoding="utf-8")
        command = [sys.executable, str(TOOL), "--input", str(source)]
        first = subprocess.run(
            command, text=True, capture_output=True, check=False
        )
        second = subprocess.run(
            command, text=True, capture_output=True, check=False
        )
        if first.returncode != 0 or second.returncode != 0:
            fail(f"shadow CLI failed: {first.stdout} {first.stderr}")
        if first.stdout != second.stdout:
            fail("shadow CLI output was not byte-stable")
        report = json.loads(first.stdout)
        if report["decision"] != "EQUIVALENT":
            fail(f"shadow CLI emitted wrong decision: {report}")
        if report["kind"] != "context-shadow-equivalence-report":
            fail(f"shadow CLI emitted wrong kind: {report}")


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
            fail(f"shadow gate gained provider/network dependency: {forbidden}")


def main() -> int:
    tests = [
        test_identical_material_actions_are_equivalent,
        test_canary_and_control_fail_closed,
        test_observation_mapping_fail_closed,
        test_telemetry_identity_fail_closed,
        test_swapped_observation_identity_is_rejected,
        test_action_trace_structure_fail_closed,
        test_terminal_complete_contract_fail_closed,
        test_block_and_wait_actions_are_compared_not_rejected,
        test_content_bearing_fields_are_rejected,
        test_material_action_or_target_divergence_fails_closed,
        test_cli_output_is_deterministic_and_content_free,
        test_source_has_no_provider_or_network_dependency,
    ]
    for test in tests:
        test()
    print("CONTEXT_SHADOW_GATE_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
