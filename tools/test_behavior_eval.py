#!/usr/bin/env python3
"""Regressions for the behavior-eval parser, evaluator, live runner, and rollout gate."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "behavior_eval.py"

_SPEC = importlib.util.spec_from_file_location("behavior_eval", TOOL)
assert _SPEC and _SPEC.loader
behavior_eval = importlib.util.module_from_spec(_SPEC)
sys.modules["behavior_eval"] = behavior_eval
_SPEC.loader.exec_module(behavior_eval)


def _fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def _run_document() -> dict:
    catalog = behavior_eval.load_catalog()
    document = {
        "schema_version": 1,
        "kind": "behavior-eval-run",
        "runner": "deterministic",
        "head": "b" * 40,
        "provider": None,
        "model": None,
        "scenarios": [
            {
                "id": item["id"],
                "mandatory": True,
                "safety": True,
                "status": "PASS",
                "evidence": "CONTEXT_ROUTING_OK",
            }
            for item in catalog
        ],
    }
    return behavior_eval.parse_document(document)


def test_catalog_rejects_missing_id() -> None:
    loaded = yaml.safe_load((ROOT / "evals/behavior/scenarios.yaml").read_text(encoding="utf-8"))
    del loaded["scenarios"][0]["id"]
    try:
        behavior_eval._schema_error(behavior_eval._validator(behavior_eval.SCENARIO_SCHEMA_PATH), loaded)
    except behavior_eval.EvalError as exc:
        if exc.code != "SCHEMA_INVALID":
            _fail(f"catalog missing id returned {exc.code}")
    else:
        _fail("catalog missing id was accepted")


def test_catalog_rejects_prompt_field() -> None:
    loaded = yaml.safe_load((ROOT / "evals/behavior/scenarios.yaml").read_text(encoding="utf-8"))
    loaded["scenarios"][0]["prompt"] = "do not store this"
    try:
        behavior_eval._schema_error(behavior_eval._validator(behavior_eval.SCENARIO_SCHEMA_PATH), loaded)
    except behavior_eval.EvalError as exc:
        if exc.code != "PROHIBITED_RESULT_FIELD" and exc.code != "SCHEMA_INVALID":
            _fail(f"catalog prompt returned {exc.code}")
    else:
        _fail("catalog prompt field was accepted")


def test_catalog_requires_the_benchmark_set() -> None:
    ids = [item["id"] for item in behavior_eval.load_catalog()]
    if tuple(ids) != behavior_eval.REQUIRED_SCENARIO_IDS:
        _fail("catalog scenario ids drifted")


def test_result_rejects_prohibited_and_freeform_fields() -> None:
    document = _run_document()
    leaked = dict(document)
    leaked["prompt"] = "secret prompt"
    try:
        behavior_eval.parse_document(leaked)
    except behavior_eval.EvalError as exc:
        if exc.code != "PROHIBITED_RESULT_FIELD":
            _fail(f"prohibited field returned {exc.code}")
    else:
        _fail("prompt field was accepted")
    freeform = json.loads(json.dumps(document))
    freeform["scenarios"][0]["evidence"] = "raw tool payload"
    try:
        behavior_eval.parse_document(freeform)
    except behavior_eval.EvalError as exc:
        if exc.code != "SCHEMA_INVALID":
            _fail(f"freeform evidence returned {exc.code}")
    else:
        _fail("freeform evidence was accepted")


def test_gate_semantics() -> None:
    document = _run_document()
    head = document["head"]
    passed = behavior_eval.evaluate_gate(document, expected_head=head, baseline_status="PASS")
    if passed["status"] != "PASS" or passed["reasons"]:
        _fail("clean gate did not pass")
    failed = json.loads(json.dumps(document))
    failed["scenarios"][0]["status"] = "FAIL"
    failed["scenarios"][0]["evidence"] = "MISSING_CONTRACT"
    blocked = behavior_eval.evaluate_gate(failed, expected_head=head, baseline_status="PASS")
    if blocked["status"] != "BLOCK" or "MANDATORY_FAIL:BEH-CTX-001" not in blocked["reasons"]:
        _fail("mandatory FAIL did not block")
    held = json.loads(json.dumps(document))
    held["scenarios"][1]["status"] = "BLOCK"
    held["scenarios"][1]["evidence"] = "CHECKER_ERROR"
    held_gate = behavior_eval.evaluate_gate(held, expected_head=head, baseline_status="PASS")
    if "MANDATORY_BLOCK:BEH-WP-002" not in held_gate["reasons"]:
        _fail("mandatory BLOCK did not block")
    baseline = behavior_eval.evaluate_gate(document, expected_head=head, baseline_status="FAIL")
    if "DETERMINISTIC_BASELINE_REGRESSION" not in baseline["reasons"]:
        _fail("baseline regression did not block")
    missing = behavior_eval.evaluate_gate(document, expected_head=head, baseline_status="MISSING")
    if "MISSING_BASELINE_EVIDENCE" not in missing["reasons"]:
        _fail("missing baseline evidence did not block")
    stale = behavior_eval.evaluate_gate(document, expected_head="c" * 40, baseline_status="PASS")
    if stale["status"] != "BLOCK" or "MISSING_EXACT_HEAD" not in stale["reasons"]:
        _fail("head mismatch did not block")


def test_gate_rejects_tampered_benchmark_results() -> None:
    head = "b" * 40
    fake = {
        "schema_version": 1,
        "kind": "behavior-eval-run",
        "runner": "deterministic",
        "head": head,
        "provider": None,
        "model": None,
        "scenarios": [
            {
                "id": "BEH-FAKE-999",
                "mandatory": False,
                "safety": False,
                "status": "PASS",
                "evidence": "CONTEXT_ROUTING_OK",
            }
        ],
    }
    blocked = behavior_eval.evaluate_gate(fake, expected_head=head, baseline_status="PASS")
    if blocked["status"] != "BLOCK" or "UNKNOWN_SCENARIO:BEH-FAKE-999" not in blocked["reasons"]:
        _fail("incomplete unknown benchmark passed the gate")
    if any(not reason.startswith("MISSING_SCENARIO:") for reason in blocked["reasons"] if reason.startswith("MISSING_")):
        _fail("missing-scenario reasons drifted")
    missing = {reason for reason in blocked["reasons"] if reason.startswith("MISSING_SCENARIO:")}
    if missing != {f"MISSING_SCENARIO:{scenario_id}" for scenario_id in behavior_eval.REQUIRED_SCENARIO_IDS}:
        _fail("gate did not require every canonical scenario")

    document = _run_document()
    duplicated = json.loads(json.dumps(document))
    duplicated["scenarios"].append(json.loads(json.dumps(duplicated["scenarios"][0])))
    duplicate_gate = behavior_eval.evaluate_gate(
        duplicated,
        expected_head=document["head"],
        baseline_status="PASS",
    )
    if duplicate_gate["status"] != "BLOCK" or "DUPLICATE_SCENARIO:BEH-CTX-001" not in duplicate_gate["reasons"]:
        _fail("duplicate scenario passed the gate")

    mutated = json.loads(json.dumps(document))
    mutated["scenarios"][0]["mandatory"] = False
    mutated["scenarios"][0]["safety"] = False
    mutated_gate = behavior_eval.evaluate_gate(mutated, expected_head=document["head"], baseline_status="PASS")
    if mutated_gate["status"] != "BLOCK" or "SCENARIO_CONTRACT_MUTATED:BEH-CTX-001" not in mutated_gate["reasons"]:
        _fail("mutated mandatory and safety flags passed the gate")


def test_context_fails_without_unrelated_repository_rule() -> None:
    text = (ROOT / "ai/AGENT_BASE.md").read_text(encoding="utf-8").replace(
        "Never scan unrelated repositories",
        "Scan every nearby checkout",
    )
    try:
        behavior_eval._require_tokens(text, ("Never scan unrelated repositories",))
    except behavior_eval.EvalError as exc:
        if exc.code != "MISSING_CONTRACT":
            _fail(f"context regression returned {exc.code}")
    else:
        _fail("removed unrelated-repository rule was accepted")


def test_work_packet_boundaries() -> None:
    actual = "d" * 40
    selected = behavior_eval.select_work_packet(
        [behavior_eval._packet(permission="write", author_association="NONE")],
        target_repo="datarelay-labs/engineering-system",
        branch="feat/example",
        actual_head=actual,
    )
    if selected["execution_head"] != actual:
        _fail("selector used packet HEAD instead of actual HEAD")
    if selected["permission"] != "write":
        _fail("trusted write permission was rejected")
    cases = {
        "read": "WORK_PACKET_AUTHOR_UNTRUSTED",
        "scope": "WORK_PACKET_SCOPE_MISMATCH",
        "many": "WORK_PACKET_SELECTION_AMBIGUOUS",
    }
    packets = {
        "read": [behavior_eval._packet(permission="read", author_association="OWNER")],
        "scope": [behavior_eval._packet(owner_intent="Ship the rollout gate", next_action="NONE")],
        "many": [behavior_eval._packet(), behavior_eval._packet()],
    }
    for name, expected in cases.items():
        try:
            behavior_eval.select_work_packet(
                packets[name],
                target_repo="datarelay-labs/engineering-system",
                branch="feat/example",
                actual_head=actual,
            )
        except behavior_eval.PacketSelectionError as exc:
            if exc.code != expected:
                _fail(f"{name} returned {exc.code}")
        else:
            _fail(f"{name} was accepted")


def test_affected_parser_fails_closed() -> None:
    tester = behavior_eval._load_tool("engineering-test.py", "engineering_test_regression")
    try:
        tester.parse_status_z(b"bogus")
    except SystemExit:
        return
    _fail("malformed status was accepted")




def test_wait_state_transitions() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch
    import shutil
    import tempfile

    outcome = behavior_eval.check_wait(ROOT)
    assert outcome == {"status": "PASS", "evidence": "WAIT_PLANNER_CONTRACT_OK"}, outcome
    # No AGENTS/prose file is needed to prove deterministic planner behavior.
    with tempfile.TemporaryDirectory() as tmp:
        isolated = Path(tmp)
        shutil.copytree(ROOT / "tools/fixtures/coordinator", isolated / "tools/fixtures/coordinator")
        assert behavior_eval.check_wait(isolated) == outcome
    real = behavior_eval._load_tool("coordinator.py", "wait_negative_control")
    def duplicate_notification(facts):
        result = real.plan(facts)
        result["notification_disposition"] = "SEND"
        return result
    with patch.object(behavior_eval, "_load_tool", return_value=SimpleNamespace(plan=duplicate_notification)):
        assert behavior_eval.check_wait(ROOT)["status"] == "FAIL"
    def side_effect(facts):
        result = real.plan(facts)
        result["mutates_github"] = True
        return result
    with patch.object(behavior_eval, "_load_tool", return_value=SimpleNamespace(plan=side_effect)):
        assert behavior_eval.check_wait(ROOT)["status"] == "FAIL"


def test_trust_checker_does_not_import_verification_fixtures() -> None:
    text = TOOL.read_text(encoding="utf-8")
    if "test_verification_contract" in text:
        _fail("behavior eval depends on verification test fixtures")
    if "probe_failures(" in text:
        _fail("behavior eval executes verification probe fixtures")


def test_deterministic_run_passes() -> None:
    document = behavior_eval.run_deterministic(ROOT)
    encoded = json.dumps(document)
    failed = [item for item in document["scenarios"] if item["status"] != "PASS"]
    if failed:
        _fail("deterministic scenarios did not pass: " + ",".join(item["id"] + ":" + item["evidence"] for item in failed))
    if len(document["scenarios"]) != len(behavior_eval.REQUIRED_SCENARIO_IDS):
        _fail("deterministic run dropped scenarios")
    if document["head"] != behavior_eval.git_head(ROOT):
        _fail("deterministic run recorded a different HEAD")
    for token in ("prompt", "stdout", "Secret", "tool_payload"):
        if token in encoded:
            _fail(f"deterministic result contained {token}")
    gate = behavior_eval.evaluate_gate(document, expected_head=document["head"], baseline_status="PASS")
    if gate["status"] != "PASS":
        _fail("deterministic gate did not pass")


def main() -> None:
    test_catalog_rejects_missing_id()
    test_catalog_rejects_prompt_field()
    test_catalog_requires_the_benchmark_set()
    test_result_rejects_prohibited_and_freeform_fields()
    test_gate_semantics()
    test_gate_rejects_tampered_benchmark_results()
    test_context_fails_without_unrelated_repository_rule()
    test_work_packet_boundaries()
    test_affected_parser_fails_closed()
    test_wait_state_transitions()
    test_trust_checker_does_not_import_verification_fixtures()
    test_deterministic_run_passes()
    print("PASS behavior eval framework")


if __name__ == "__main__":
    main()
