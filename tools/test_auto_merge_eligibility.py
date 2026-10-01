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

import auto_merge_eligibility as gate  # noqa: E402

TOOL = ROOT / "tools" / "auto_merge_eligibility.py"
SCHEMA = json.loads(
    (ROOT / "schemas" / "auto-merge-eligibility.schema.json").read_text(encoding="utf-8")
)
RESULT_SCHEMA = json.loads(
    (ROOT / "schemas" / "auto-merge-eligibility-result.schema.json").read_text(encoding="utf-8")
)
VALIDATOR = Draft202012Validator(SCHEMA)
RESULT_VALIDATOR = Draft202012Validator(RESULT_SCHEMA)
HEAD = "a" * 40
OTHER = "b" * 40
WORKSTREAM = "engineering-system-1.7-conditional-auto-merge-trust-gate"
REVISION = 2


def empty_role() -> dict:
    return {"tests": [], "runtime": [], "skills": [], "profiles": []}


def verification_manifest(*, level: str = "T5", automate: bool = True) -> dict:
    drive = empty_role()
    drive["tests"] = ["ENG-STATIC-001"]
    observe = empty_role()
    observe["runtime"] = ["health"]
    return {
        "version": 1,
        "automation_eligible": ["runtime-health"] if automate else [],
        "features": [
            {
                "id": "runtime-health",
                "oracle": "RUNTIME_HEALTH_OBSERVED",
                "minimum_level": level,
                "domains": ["operations-contract"],
                "references": {
                    "launch": empty_role(),
                    "drive": drive,
                    "observe": observe,
                    "cleanup": empty_role(),
                },
            }
        ],
    }


def verification_receipt() -> dict:
    return {
        "schema_version": 1,
        "kind": "trust-evidence-receipt",
        "target_repo": "datarelay-labs/engineering-system",
        "workstream": WORKSTREAM,
        "intent_revision": REVISION,
        "subject_head": HEAD,
        "feature_id": "runtime-health",
        "oracle": "RUNTIME_HEALTH_OBSERVED",
        "runtime_subject": "worktree:canonical",
        "items": [
            {
                "authority": "self_report",
                "id": "health",
                "status": "PASS",
                "subject_head": HEAD,
                "intent_revision": REVISION,
                "reference": "self_report/health",
                "external_digest": "c" * 64,
            }
        ],
    }


def request() -> dict:
    return {
        "schema_version": 1,
        "subject": {
            "target_repo": "datarelay-labs/engineering-system",
            "workstream": WORKSTREAM,
            "intent_revision": REVISION,
            "pr_number": 200,
            "base_branch": "main",
            "head_branch": "feat/auto-merge-gate",
            "head_sha": HEAD,
            "task_kind": "IMPLEMENTATION",
            "change_risk": "HIGH",
            "policy_profile": "engineering-system",
        },
        "verification": {
            "receipt": verification_receipt(),
            "manifest": verification_manifest(),
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


def evidence(authority: str, item_id: str, *, status: str = "PASS") -> dict:
    return {
        "authority": authority,
        "id": item_id,
        "status": status,
        "subject_head": HEAD,
        "intent_revision": REVISION,
    }


def trusted_boundary(*, evidence_items: list[dict] | None = None, eligible: bool = True):
    module = gate.verification_module()
    proven = evidence_items
    if proven is None:
        proven = [
            evidence("test", "ENG-STATIC-001"),
            evidence("ci", "exact-head"),
            evidence("runtime", "health"),
        ]
    body = {
        "schema_version": 1,
        "kind": "trust-evidence-boundary",
        "provenance": "coordinator-boundary",
        "target_repo": "datarelay-labs/engineering-system",
        "workstream": WORKSTREAM,
        "intent_revision": REVISION,
        "subject_head": HEAD,
        "runtime_subject": "worktree:canonical",
        "evidence": proven,
        "verifier": {
            "change_risk": "HIGH",
            "implementer": {"identity": "impl-1", "context_id": "ctx-impl"},
            "verifier": {"identity": "ver-1", "context_id": "ctx-ver"},
            "oracle_id": "ENG-ORACLE-001",
            "oracle_result": "PASS",
            "ci_subject_id": "pr-174",
            "ci_result": "PASS",
        },
        "automation_policy": {"feature_id": "runtime-health", "eligible": eligible},
    }
    return module.TrustedCoordinatorBoundary(body)


def assert_no_authority(report: dict) -> None:
    assert report["authorizes_merge"] is False
    assert report["external_mutation"] is False
    assert report["executes_commands"] is False
    assert report["performs_network_io"] is False
    assert not list(RESULT_VALIDATOR.iter_errors(report))


def test_schema_and_happy_path() -> None:
    payload = request()
    assert not list(VALIDATOR.iter_errors(payload))
    report = gate.evaluate(payload, boundary=trusted_boundary())
    assert report["decision"] == "ELIGIBLE"
    assert report["reason_class"] == "ALL_GATES_PASS"
    assert report["eligible"] is True
    assert_no_authority(report)
    assert gate.evaluate(copy.deepcopy(payload), boundary=trusted_boundary()) == report


def test_raw_json_and_boundary_copies_cannot_elevate() -> None:
    payload = request()
    raw = gate.evaluate(payload)
    assert raw["decision"] == "BLOCK"
    assert raw["reason_class"] == "UNTRUSTED_BOUNDARY"
    assert_no_authority(raw)

    boundary_dict = trusted_boundary().payload()
    parsed = json.loads(json.dumps(boundary_dict))
    module = gate.verification_module()
    for candidate in (boundary_dict, parsed):
        report = gate.evaluate(payload, boundary=candidate)
        assert report["decision"] == "BLOCK"
        assert report["reason_class"] == "UNTRUSTED_BOUNDARY"
        assert not isinstance(candidate, module.TrustedCoordinatorBoundary)
        assert_no_authority(report)


def test_forged_t5_labels_and_empty_boundary_still_block() -> None:
    payload = request()
    payload["trust"] = {
        "decision": "PASS",
        "achieved": "T5",
        "automation_eligible": "YES",
    }
    malformed = gate.evaluate(payload, boundary=trusted_boundary(evidence_items=[]))
    assert malformed["decision"] == "BLOCK"
    assert malformed["reason_class"] == "MALFORMED"
    assert_no_authority(malformed)

    payload = request()
    report = gate.evaluate(payload, boundary=trusted_boundary(evidence_items=[]))
    assert report["decision"] == "BLOCK"
    assert report["reason_class"] != "ALL_GATES_PASS"
    assert_no_authority(report)


def test_actual_verification_assessment_controls_t5() -> None:
    payload = request()
    payload["verification"]["manifest"] = verification_manifest(level="T4", automate=False)
    report = gate.evaluate(payload, boundary=trusted_boundary(eligible=False))
    assert report["decision"] == "BLOCK"
    assert report["reason_class"] != "ALL_GATES_PASS"
    assert_no_authority(report)

    payload = request()
    bad = trusted_boundary(
        evidence_items=[
            evidence("test", "ENG-STATIC-001"),
            evidence("ci", "exact-head"),
        ]
    )
    report = gate.evaluate(payload, boundary=bad)
    assert report["decision"] == "BLOCK"
    assert report["reason_class"] == "MISSING_RUNTIME"
    assert_no_authority(report)


def test_stale_identity_and_boundary_block() -> None:
    payload = request()
    payload["observed"]["head_sha"] = OTHER
    assert gate.evaluate(payload, boundary=trusted_boundary())["reason_class"] == "STALE_HEAD"

    payload = request()
    boundary = trusted_boundary()
    boundary.payload()["subject_head"] = OTHER
    report = gate.evaluate(payload, boundary=boundary)
    assert report["decision"] == "BLOCK"
    assert report["reason_class"] != "ALL_GATES_PASS"
    assert_no_authority(report)

    payload = request()
    payload["observed"]["pr_number"] = 201
    assert gate.evaluate(payload, boundary=trusted_boundary())["reason_class"] == "STALE_IDENTITY"


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
        report = gate.evaluate(payload, boundary=trusted_boundary())
        assert report["decision"] == "DENY"
        assert report["reason_class"] == reason
        assert_no_authority(report)

    payload = request()
    payload["policy"]["ruleset_allows"] = False
    assert gate.evaluate(payload, boundary=trusted_boundary())["reason_class"] == "RULESET_FORBIDS"

    payload = request()
    payload["policy"]["auto_merge_enabled"] = False
    report = gate.evaluate(payload, boundary=trusted_boundary())
    assert report["decision"] == "DENY"
    assert report["reason_class"] == "AUTO_MERGE_DISABLED"
    assert_no_authority(report)


def test_ci_and_review_are_exact_head_and_current() -> None:
    payload = request()
    payload["observed"]["ci"]["head_sha"] = OTHER
    assert gate.evaluate(payload, boundary=trusted_boundary())["reason_class"] == "STALE_CI"

    payload = request()
    payload["observed"]["ci"]["status"] = "UNKNOWN"
    assert gate.evaluate(payload, boundary=trusted_boundary())["reason_class"] == "CI_NOT_READY"

    payload = request()
    payload["observed"]["ci"]["status"] = "FAIL"
    report = gate.evaluate(payload, boundary=trusted_boundary())
    assert report["decision"] == "DENY"
    assert report["reason_class"] == "CI_FAILED"

    payload = request()
    payload["observed"]["review"]["head_sha"] = OTHER
    assert gate.evaluate(payload, boundary=trusted_boundary())["reason_class"] == "STALE_REVIEW"

    payload = request()
    payload["observed"]["review"]["status"] = "PENDING"
    assert gate.evaluate(payload, boundary=trusted_boundary())["reason_class"] == "REVIEW_NOT_READY"

    payload = request()
    payload["observed"]["review"]["status"] = "FAIL"
    report = gate.evaluate(payload, boundary=trusted_boundary())
    assert report["decision"] == "DENY"
    assert report["reason_class"] == "REVIEW_FAILED"

    payload = request()
    payload["observed"]["review"]["unresolved_threads"] = 1
    report = gate.evaluate(payload, boundary=trusted_boundary())
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
        report = gate.evaluate(payload, boundary=trusted_boundary())
        assert report["decision"] == "BLOCK"
        assert report["reason_class"] == reason
        assert_no_authority(report)


def test_malformed_shapes_return_structured_block() -> None:
    payload = request()
    payload["subject"] = []
    report = gate.evaluate(payload)
    assert report["decision"] == "BLOCK"
    assert report["reason_class"] == "MALFORMED"
    assert report["target_repo"] == ""
    assert_no_authority(report)

    payload = request()
    del payload["observed"]["review"]
    report = gate.evaluate(payload)
    assert report["decision"] == "BLOCK"
    assert report["reason_class"] == "MALFORMED"
    assert_no_authority(report)

    malformed_fields = {
        "target_repo": 123,
        "workstream": False,
        "intent_revision": "bad",
        "pr_number": True,
        "head_sha": ["bad"],
    }
    payload = request()
    payload["subject"].update(malformed_fields)
    report = gate.evaluate(payload)
    assert report["decision"] == "BLOCK"
    assert report["reason_class"] == "MALFORMED"
    assert report["target_repo"] == ""
    assert report["workstream"] == ""
    assert report["intent_revision"] == 0
    assert report["pr_number"] == 0
    assert report["subject_head"] == ""
    assert not list(RESULT_VALIDATOR.iter_errors(report))
    assert_no_authority(report)


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
        'add_argument("--boundary"',
        "args.boundary",
    ):
        assert token not in text


def test_cli_is_negative_only() -> None:
    payload = request()
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
    report = json.loads(completed.stdout)
    assert report["decision"] == "BLOCK"
    assert report["reason_class"] == "UNTRUSTED_BOUNDARY"
    assert_no_authority(report)


def main() -> None:
    test_schema_and_happy_path()
    test_raw_json_and_boundary_copies_cannot_elevate()
    test_forged_t5_labels_and_empty_boundary_still_block()
    test_actual_verification_assessment_controls_t5()
    test_stale_identity_and_boundary_block()
    test_policy_denials()
    test_ci_and_review_are_exact_head_and_current()
    test_mutation_replay_and_ambiguity_block()
    test_malformed_shapes_return_structured_block()
    test_implementation_has_no_execution_or_network_surface()
    test_cli_is_negative_only()
    print("PASS conditional auto-merge eligibility gate")


if __name__ == "__main__":
    main()
