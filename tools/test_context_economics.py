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
TOOL = TOOLS / "context_economics.py"
sys.path.insert(0, str(TOOLS))

import test_context_shadow_gate as fixtures

spec = importlib.util.spec_from_file_location("context_economics_tested", TOOL)
assert spec and spec.loader
economics = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = economics
spec.loader.exec_module(economics)


def fail(message: str) -> None:
    raise AssertionError(message)


def expect_error(code: str, callback) -> None:
    try:
        callback()
    except economics.EconomicsError as exc:
        if str(exc) != code:
            fail(f"expected {code}, got {exc}")
    else:
        fail(f"expected failure {code}")


def envelope() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "context-economics-comparison",
        "expected_control_arm_id": "baseline",
        "shadow": fixtures.shadow_document(),
    }


def _binding(doc: dict[str, object], arm_id: str, case_id: str) -> dict[str, object]:
    for item in doc["shadow"]["canary"]["telemetry_bindings"]:
        if item["arm_id"] == arm_id and item["case_id"] == case_id:
            return item
    fail(f"binding missing: {arm_id}/{case_id}")


def _run(doc: dict[str, object], arm_id: str, case_id: str) -> dict[str, object]:
    for item in doc["shadow"]["canary"]["run_set"]["records"]:
        if item["ARM_ID"] == arm_id and item["CASE_ID"] == case_id:
            return item
    fail(f"run missing: {arm_id}/{case_id}")


def set_arm_cost(doc: dict[str, object], arm_id: str, per_run_cost: float) -> None:
    for case_id in ("CTX-SHADOW-001", "CTX-SHADOW-002"):
        binding = _binding(doc, arm_id, case_id)
        binding["telemetry"]["usage"]["cost"] = per_run_cost
        _run(doc, arm_id, case_id)["MODEL_COST"] = per_run_cost


def set_candidate_compensation(doc: dict[str, object]) -> None:
    for case_id in ("CTX-SHADOW-001", "CTX-SHADOW-002"):
        record = _binding(doc, "candidate", case_id)["telemetry"]
        record["usage"].update(
            {
                "input_tokens": 80,
                "output_tokens": 30,
                "cache_read_tokens": 60,
                "cache_write_tokens": 5,
            }
        )
        record["counts"]["retries"] = 1
        record["counts"]["rereads"] = 2
        record["counts"]["compactions"] = 1
        record["counts"]["review_rework"] = 1
        run = _run(doc, "candidate", case_id)
        run["RETRIES"] = 1
        run["REVIEW_REWORK"] = 1


def candidate(report: dict[str, object], arm_id: str = "candidate") -> dict[str, object]:
    for item in report["candidates"]:
        if item["arm_id"] == arm_id:
            return item
    fail(f"candidate missing: {arm_id}")


def test_lower_cost_with_compensation_is_reported_factually() -> None:
    doc = envelope()
    set_arm_cost(doc, "candidate", 0.75)
    set_candidate_compensation(doc)
    report = economics.evaluate_economics(doc)
    item = candidate(report)

    if report["authority"] != "EVIDENCE_ONLY" or report["promotion_authority"] != "NONE":
        fail(f"authority drifted: {report}")
    if report["control_arm_id"] != "baseline":
        fail(f"control binding drifted: {report}")
    if report["run_set_digest"] != economics.shadow_gate.evaluate_shadow(doc["shadow"])["run_set_digest"]:
        fail("run-set digest binding drifted")
    expected = {
        "provider_cost_delta": "-0.5",
        "provider_cost_delta_percent": "-25",
        "original_context_bytes_delta": 0,
        "kept_context_bytes_delta": -800,
        "input_tokens_delta": -40,
        "output_tokens_delta": 20,
        "cache_read_tokens_delta": 40,
        "cache_write_tokens_delta": -10,
        "retries_delta": 2,
        "rereads_delta": 4,
        "compactions_delta": 2,
        "review_rework_delta": 2,
        "rework_delta": 2,
    }
    for key, value in expected.items():
        if item[key] != value:
            fail(f"{key} expected {value}, got {item[key]}")

    encoded = json.dumps(report, sort_keys=True)
    for forbidden in (
        "winner", "ranking", "recommendation", "effective_tokens",
        "prompt", "tool_output", "source_text", "/home/", "ollama", "paritok",
    ):
        if forbidden in encoded.lower():
            fail(f"report leaked or inferred forbidden field: {forbidden}")


def test_same_and_higher_cost_are_not_relabelled_as_winners() -> None:
    same = envelope()
    report = economics.evaluate_economics(same)
    item = candidate(report)
    if item["provider_cost_delta"] != "0" or item["provider_cost_delta_percent"] != "0":
        fail(f"same-cost delta drifted: {item}")

    higher = envelope()
    set_arm_cost(higher, "candidate", 1.25)
    item = candidate(economics.evaluate_economics(higher))
    if item["provider_cost_delta"] != "0.5" or item["provider_cost_delta_percent"] != "25":
        fail(f"higher-cost delta drifted: {item}")


def test_zero_cost_control_keeps_percent_unknown() -> None:
    doc = envelope()
    set_arm_cost(doc, "baseline", 0.0)
    set_arm_cost(doc, "candidate", 0.25)
    item = candidate(economics.evaluate_economics(doc))
    if item["provider_cost_delta"] != "0.5":
        fail(f"zero-control absolute delta drifted: {item}")
    if item["provider_cost_delta_percent"] is not None:
        fail(f"zero-control percent was invented: {item}")


def test_shadow_and_control_bindings_fail_closed() -> None:
    doc = envelope()
    doc["expected_control_arm_id"] = "candidate"
    expect_error("CONTROL_ARM_MISMATCH", lambda: economics.evaluate_economics(doc))

    doc = envelope()
    _binding(doc, "candidate", "CTX-SHADOW-001")["telemetry"]["profile"]["model"] = "model-y"
    expect_error(
        "SHADOW_INELIGIBLE:CANARY_INELIGIBLE:PROFILE_MISMATCH",
        lambda: economics.evaluate_economics(doc),
    )

    doc = envelope()
    obs = fixtures.observation(doc["shadow"], "candidate", "CTX-SHADOW-001")
    obs["actions"][1] = {"action": "TEST", "target": "TEST"}
    expect_error(
        "SHADOW_INELIGIBLE:ACTION_TRACE_DIVERGED:candidate:CTX-SHADOW-001",
        lambda: economics.evaluate_economics(doc),
    )


def test_envelope_and_schema_are_strict() -> None:
    doc = envelope()
    doc["note"] = "freeform"
    expect_error("ENVELOPE_INVALID", lambda: economics.evaluate_economics(doc))

    doc = envelope()
    doc["expected_control_arm_id"] = "../baseline"
    expect_error("CONTROL_ARM_INVALID", lambda: economics.evaluate_economics(doc))

    report = economics.evaluate_economics(envelope())
    broken = copy.deepcopy(report)
    broken["winner"] = "candidate"
    expect_error("REPORT_SCHEMA_INVALID", lambda: economics._validate_output(broken))


def test_cli_and_source_are_offline_non_authorizing() -> None:
    source = TOOL.read_text(encoding="utf-8").lower()
    for forbidden in ("subprocess", "urllib", "requests", "http://", "https://", "ollama", "paritok"):
        if forbidden in source:
            fail(f"economics helper gained forbidden runtime/network path: {forbidden}")
    for required in (
        '"authority": "EVIDENCE_ONLY"',
        '"promotion_authority": "NONE"',
        "provider_cost_delta_percent",
        "cache_read_tokens",
        "output_tokens",
        "retries",
        "_delta",
    ):
        if required not in TOOL.read_text(encoding="utf-8"):
            fail(f"economics helper missing contract token: {required}")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "input.json"
        path.write_text(json.dumps(envelope()), encoding="utf-8")
        completed = subprocess.run(
            ["python3", str(TOOL), "--input", str(path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            fail(f"CLI rejected valid economics input: {completed.stdout} {completed.stderr}")
        report = json.loads(completed.stdout)
        if report["kind"] != "context-net-economics-report":
            fail(f"CLI report kind drifted: {report}")


def main() -> None:
    test_lower_cost_with_compensation_is_reported_factually()
    test_same_and_higher_cost_are_not_relabelled_as_winners()
    test_zero_cost_control_keeps_percent_unknown()
    test_shadow_and_control_bindings_fail_closed()
    test_envelope_and_schema_are_strict()
    test_cli_and_source_are_offline_non_authorizing()
    print("CONTEXT_ECONOMICS_TESTS=PASS")


if __name__ == "__main__":
    main()
