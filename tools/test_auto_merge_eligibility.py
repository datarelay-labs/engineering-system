#!/usr/bin/env python3
"""Deterministic regressions for the pure conditional auto-merge eligibility gate."""
from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from auto_merge_eligibility import evaluate  # noqa: E402

TOOL = ROOT / "tools" / "auto_merge_eligibility.py"
SCHEMA = json.loads(
    (ROOT / "schemas" / "auto-merge-eligibility.schema.json").read_text(encoding="utf-8")
)
VALIDATOR = Draft202012Validator(SCHEMA)
HEAD = "a" * 40
OTHER = "b" * 40


def request() -> dict:
    return {
        "schema_version": 1,
        "subject": {
            "target_repo": "datarelay-labs/engineering-system",
            "workstream": "engineering-system-1.7-conditional-auto-merge-trust-gate",
            "intent_revision": 2,
            "pr_number": 200,
            "base_branch": "main",
            "head_branch": "feat/auto-merge-gate",
            "head_sha": HEAD,
            "task_kind": "IMPLEMENTATION",
            "change_risk": "HIGH",
            "policy_profile": "engineering-system",
        },
        "trust": {
            "decision": "PASS",
            "achieved": "T5",
            "automation_eligible": "YES",
            "target_repo": "datarelay-labs/engineering-system",
            "workstream": "engineering-system-1.7-conditional-auto-merge-trust-gate",
            "intent_revision": 2,
            "subject_head": HEAD,
            "external_mutation": "NO",
        },
        "policy": {
            "allowed_repositories": ["datarelay-labs/engineering-system"],
            "allowed_profiles": ["engineering-system"],
            "allowed_task_kinds": ["IMPLEMENTATION"],
            "allowed_change_risks": ["HIGH"],
            "allowed_base_branches": ["main"],
            "auto_merge_enabled": True,
            "ruleset_allows": True,
        },
        "observed": {
            "target_repo": "datarelay-labs/engineering-system",
            "pr_number": 200,
            "base_branch": "main",
            "head_branch": "feat/auto-merge-gate",
            "head_sha": HEAD,
            "ci": {"status": "PASS", "head_sha": HEAD},
            "review": {"status": "PASS", "head_sha": HEAD, "unresolved_threads": 0},
            "mutation_state": "CLEAN",
        },
    }


def assert_no_authority(report: dict) -> None:
    assert report["authorizes_merge"] is False
    assert report["external_mutation"] is False
    assert report["executes_commands"] is False
    assert report["performs_network_io"] is False


def test_schema_and_happy_path() -> None:
    payload = request()
    assert not list(VALIDATOR.iter_errors(payload))
    report = evaluate(payload)
    assert report["decision"] == "ELIGIBLE"
    assert report["reason_class"] == "ALL_GATES_PASS"
    assert report["eligible"] is True
    assert_no_authority(report)
    assert evaluate(copy.deepcopy(payload)) == report


def test_stale_identity_and_trust_block() -> None:
    payload = request()
    payload["observed"]["head_sha"] = OTHER
    assert evaluate(payload)["reason_class"] == "STALE_HEAD"

    payload = request()
    payload["trust"]["intent_revision"] = 1
    assert evaluate(payload)["reason_class"] == "STALE_TRUST"

    payload = request()
    payload["trust"]["subject_head"] = OTHER
    assert evaluate(payload)["reason_class"] == "STALE_TRUST"

    payload = request()
    payload["observed"]["pr_number"] = 201
    assert evaluate(payload)["reason_class"] == "STALE_IDENTITY"


def test_trust_must_be_exact_t5_automation_eligible() -> None:
    payload = request()
    payload["trust"]["decision"] = "BLOCK"
    assert evaluate(payload)["reason_class"] == "TRUST_BLOCKED"

    payload = request()
    payload["trust"]["achieved"] = "T4"
    assert evaluate(payload)["reason_class"] == "TRUST_BELOW_T5"

    payload = request()
    payload["trust"]["automation_eligible"] = "NO"
    assert evaluate(payload)["reason_class"] == "AUTOMATION_NOT_ELIGIBLE"


def test_policy_denials() -> None:
    cases = (
        ("allowed_repositories", [], "REPOSITORY_NOT_ALLOWLISTED"),
        ("allowed_profiles", [], "PROFILE_NOT_ALLOWED"),
        ("allowed_task_kinds", [], "TASK_KIND_NOT_ALLOWED"),
        ("allowed_change_risks", [], "CHANGE_RISK_NOT_ALLOWED"),
        ("allowed_base_branches", [], "BASE_BRANCH_NOT_ALLOWED"),
    )
    for field, value, reason in cases:
        payload = request()
        payload["policy"][field] = value
        report = evaluate(payload)
        assert report["decision"] == "DENY"
        assert report["reason_class"] == reason
        assert_no_authority(report)

    payload = request()
    payload["policy"]["ruleset_allows"] = False
    assert evaluate(payload)["reason_class"] == "RULESET_FORBIDS"

    payload = request()
    payload["policy"]["auto_merge_enabled"] = False
    report = evaluate(payload)
    assert report["decision"] == "DENY"
    assert report["reason_class"] == "AUTO_MERGE_DISABLED"



def test_ci_and_review_are_exact_head_and_current() -> None:
    payload = request()
    payload["observed"]["ci"]["head_sha"] = OTHER
    assert evaluate(payload)["reason_class"] == "STALE_CI"

    payload = request()
    payload["observed"]["ci"]["status"] = "UNKNOWN"
    assert evaluate(payload)["reason_class"] == "CI_NOT_READY"

    payload = request()
    payload["observed"]["ci"]["status"] = "FAIL"
    report = evaluate(payload)
    assert report["decision"] == "DENY"
    assert report["reason_class"] == "CI_FAILED"

    payload = request()
    payload["observed"]["review"]["head_sha"] = OTHER
    assert evaluate(payload)["reason_class"] == "STALE_REVIEW"

    payload = request()
    payload["observed"]["review"]["status"] = "PENDING"
    assert evaluate(payload)["reason_class"] == "REVIEW_NOT_READY"

    payload = request()
    payload["observed"]["review"]["status"] = "FAIL"
    report = evaluate(payload)
    assert report["decision"] == "DENY"
    assert report["reason_class"] == "REVIEW_FAILED"

    payload = request()
    payload["observed"]["review"]["unresolved_threads"] = 1
    report = evaluate(payload)
    assert report["decision"] == "DENY"
    assert report["reason_class"] == "UNRESOLVED_REVIEW_THREADS"


def test_mutation_replay_and_ambiguity_block() -> None:
    expected = {
        "IN_FLIGHT": "MUTATION_IN_FLIGHT",
        "AMBIGUOUS": "MUTATION_AMBIGUOUS",
        "REPLAY": "REPLAY",
        "UNKNOWN": "MUTATION_UNKNOWN",
    }
    for state, reason in expected.items():
        payload = request()
        payload["observed"]["mutation_state"] = state
        report = evaluate(payload)
        assert report["decision"] == "BLOCK"
        assert report["reason_class"] == reason
        assert_no_authority(report)


def test_missing_unknown_or_execution_fields_fail_closed() -> None:
    payload = request()
    del payload["observed"]["review"]
    report = evaluate(payload)
    assert report["decision"] == "BLOCK"
    assert report["reason_class"] == "MALFORMED"

    payload = request()
    payload["command"] = "gh pr merge 200"
    report = evaluate(payload)
    assert report["decision"] == "BLOCK"
    assert report["reason_class"] == "MALFORMED"

    payload = request()
    payload["policy"]["url"] = "https://example.invalid"
    report = evaluate(payload)
    assert report["decision"] == "BLOCK"
    assert report["reason_class"] == "MALFORMED"


def test_implementation_has_no_execution_or_network_surface() -> None:
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert not imports.intersection({"subprocess", "socket", "urllib", "requests", "http", "os"})

    text = TOOL.read_text(encoding="utf-8")
    for token in (
        "merge_pull_request",
        "gh api",
        "urlopen(",
        "Popen(",
        "os.system(",
        "shell=True",
    ):
        assert token not in text


def test_result_schema_and_cli_roundtrip() -> None:
    result_schema = json.loads(
        (ROOT / "schemas" / "auto-merge-eligibility-result.schema.json").read_text(
            encoding="utf-8"
        )
    )
    result_validator = Draft202012Validator(result_schema)
    payload = request()
    report = evaluate(payload)
    assert not list(result_validator.iter_errors(report))

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "request.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(TOOL), "evaluate", "--request-json", str(path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr
    cli_report = json.loads(completed.stdout)
    assert not list(result_validator.iter_errors(cli_report))
    assert cli_report["decision"] == "ELIGIBLE"
    assert_no_authority(cli_report)


def main() -> None:
    test_schema_and_happy_path()
    test_stale_identity_and_trust_block()
    test_trust_must_be_exact_t5_automation_eligible()
    test_policy_denials()
    test_ci_and_review_are_exact_head_and_current()
    test_mutation_replay_and_ambiguity_block()
    test_missing_unknown_or_execution_fields_fail_closed()
    test_implementation_has_no_execution_or_network_surface()
    test_result_schema_and_cli_roundtrip()
    print("PASS conditional auto-merge eligibility gate")


if __name__ == "__main__":
    main()

