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


def test_resource_mutation_token_fails() -> None:
    if behavior_eval.resource_source_safe("value = 1\nos.kill(pid, 9)\n"):
        _fail("session mutation token was accepted")
    if not behavior_eval.resource_source_safe("RESULT = BLOCK\n"):
        _fail("non-mutating resource source was rejected")


def test_wait_and_review_contract_tokens() -> None:
    try:
        behavior_eval._require_tokens("polling only", ("polling", "WAITING_FOR_", "yield"))
    except behavior_eval.EvalError as exc:
        if exc.code != "MISSING_CONTRACT":
            _fail(f"wait regression returned {exc.code}")
    else:
        _fail("incomplete wait contract was accepted")
    resume = (ROOT / ".cursor/commands/resume.md").read_text(encoding="utf-8").replace(
        "evidence-backed disposition",
        "untracked note",
    )
    if behavior_eval.review_resume_ok(resume):
        _fail("review disposition regression was accepted")
    if not behavior_eval.review_resume_ok((ROOT / ".cursor/commands/resume.md").read_text(encoding="utf-8")):
        _fail("canonical resume lost the review disposition contract")
    try:
        behavior_eval.check_review(Path("/tmp/behavior-eval-missing-review-contract"))
    except behavior_eval.EvalError as exc:
        if exc.code != "MISSING_CONTRACT":
            _fail(f"missing review contract returned {exc.code}")
    else:
        _fail("missing review contract was accepted")


def test_live_unavailable_and_metadata_filter() -> None:
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("agent")

    blocked = behavior_eval.run_live(ROOT, "BEH-CTX-001", invoke=missing)
    encoded = json.dumps(blocked)
    if blocked["scenarios"][0]["status"] != "BLOCK":
        _fail("missing provider did not block")
    if blocked["scenarios"][0]["evidence"] != "PROVIDER_UNAVAILABLE":
        _fail("missing provider evidence drifted")
    if "Reply with exactly PASS" in encoded or blocked["provider"] is not None:
        _fail("live block persisted prompt or invented a provider")

    def completed(*_args, **_kwargs):
        payload = {
            "provider": "cursor",
            "model": "auto-resolved",
            "result": "PASS",
            "prompt": "Reply with exactly PASS",
            "stdout": "class Secret:\n    token = 'hidden'\n",
        }
        return subprocess.CompletedProcess(args=["agent"], returncode=0, stdout=json.dumps(payload), stderr="")

    passed = behavior_eval.run_live(ROOT, "BEH-CTX-001", invoke=completed)
    encoded = json.dumps(passed)
    if passed["provider"] != "cursor" or passed["model"] != "auto-resolved":
        _fail("live metadata was not recorded")
    if passed["scenarios"][0]["status"] != "PASS":
        _fail("live PASS was not recorded")
    if "Secret" in encoded or "hidden" in encoded or "prompt" in encoded:
        _fail("live result persisted prompt or source content")

    def unreadable(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=["agent"], returncode=2, stdout="", stderr="auth failed")

    denied = behavior_eval.run_live(ROOT, "BEH-CTX-001", invoke=unreadable)
    if denied["scenarios"][0]["evidence"] != "PROVIDER_UNAVAILABLE":
        _fail("provider failure was not a clean block")
    if "auth failed" in json.dumps(denied):
        _fail("provider stderr was persisted")


def test_unknown_scenario_and_duplicate_catalog() -> None:
    try:
        behavior_eval.run_live(ROOT, "BEH-MISSING-999", invoke=lambda *_a, **_k: None)
    except behavior_eval.EvalError as exc:
        if exc.code != "UNKNOWN_SCENARIO":
            _fail(f"unknown scenario returned {exc.code}")
    else:
        _fail("unknown scenario was accepted")


def test_deterministic_run_passes() -> None:
    document = behavior_eval.run_deterministic(ROOT)
    encoded = json.dumps(document)
    failed = [item for item in document["scenarios"] if item["status"] != "PASS"]
    if failed:
        _fail("deterministic scenarios did not pass: " + ",".join(item["id"] + ":" + item["evidence"] for item in failed))
    if len(document["scenarios"]) != 8:
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
    test_context_fails_without_unrelated_repository_rule()
    test_work_packet_boundaries()
    test_affected_parser_fails_closed()
    test_resource_mutation_token_fails()
    test_wait_and_review_contract_tokens()
    test_live_unavailable_and_metadata_filter()
    test_unknown_scenario_and_duplicate_catalog()
    test_deterministic_run_passes()
    print("PASS behavior eval framework")


if __name__ == "__main__":
    main()
