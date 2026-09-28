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
TOOL = ROOT / "tools/context_learned_admission.py"

spec = importlib.util.spec_from_file_location("learned_admission_tested", TOOL)
assert spec and spec.loader
admission = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = admission
spec.loader.exec_module(admission)

PIN = {
    "candidate_id": "paritok-local",
    "source_repo": "Paritok-official/paritok-4b-v1",
    "source_commit": "2c913302073367f2402d8bc4bb1a929a3f70a030",
    "package_version": "1.3.13",
    "license": "Apache-2.0",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def expect_error(code: str, callback) -> None:
    try:
        callback()
    except admission.AdmissionError as exc:
        if str(exc) != code:
            fail(f"expected {code}, got {exc}")
    else:
        fail(f"expected failure {code}")


def ready_facts() -> dict[str, object]:
    return {
        "execution_mode": "LOCAL_ONLY",
        "endpoint_class": "LOOPBACK",
        "external_egress": "DENY",
        "protected_state_route": "BYPASS",
        "deterministic_bypass": True,
        "exact_original_recovery_verified": True,
        "identifier_preservation_verified": True,
        "cache_behavior_verified": True,
        "provider_usage_capture_ready": True,
        "live_comparability_gate_available": True,
        "shadow_equivalence_gate_available": True,
    }


def request(runtime_facts: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "context-learned-canary-admission",
        **PIN,
        "runtime_facts": ready_facts() if runtime_facts is None else runtime_facts,
    }


def test_all_verified_local_facts_are_canary_ready() -> None:
    report = admission.evaluate_admission(request())
    if report["decision"] != "CANARY_READY":
        fail(f"fully verified local candidate was not ready: {report}")
    if not report["setup_allowed"] or not report["canary_ready"]:
        fail(f"ready flags drifted: {report}")
    if report["blockers"]:
        fail(f"ready candidate retained blockers: {report}")
    if report["candidate"] != {
        **PIN,
        "integration_mode": "LOCAL_SELF_HOST",
    }:
        fail(f"pinned candidate facts drifted: {report['candidate']}")
    if not all(report["requirements"].values()):
        fail(f"ready requirements drifted: {report['requirements']}")


def test_source_pin_and_license_fail_closed() -> None:
    cases = (
        ("source_repo", "other/project", "SOURCE_REPO_MISMATCH"),
        ("source_commit", "a" * 40, "SOURCE_COMMIT_MISMATCH"),
        ("package_version", "9.9.9", "PACKAGE_VERSION_MISMATCH"),
        ("license", "MIT", "LICENSE_MISMATCH"),
    )
    for field, value, code in cases:
        doc = request()
        doc[field] = value
        expect_error(code, lambda doc=doc: admission.evaluate_admission(doc))

    doc = request()
    doc["source_commit"] = "main"
    expect_error("ENVELOPE_INVALID", lambda: admission.evaluate_admission(doc))

    doc = request()
    doc["candidate_id"] = "unknown-candidate"
    expect_error("CANDIDATE_UNKNOWN", lambda: admission.evaluate_admission(doc))


def test_setup_allowed_can_be_not_canary_ready() -> None:
    report = admission.evaluate_admission(request({}))
    if report["decision"] != "SETUP_ALLOWED":
        fail(f"incomplete runtime facts should permit setup only: {report}")
    if not report["setup_allowed"] or report["canary_ready"]:
        fail(f"setup/readiness separation drifted: {report}")
    expected = [
        code
        for _field, _expected, code in admission.RUNTIME_REQUIREMENTS
    ] + [code for _field, code in admission.BOOLEAN_REQUIREMENTS]
    if report["blockers"] != expected:
        fail(f"incomplete runtime blocker order drifted: {report['blockers']}")
    if any(report["requirements"].values()):
        fail(f"empty runtime facts unexpectedly passed requirements: {report}")


def test_runtime_mode_boundaries_block_readiness() -> None:
    cases = (
        ("execution_mode", "HOSTED", "EXECUTION_MODE_NOT_LOCAL"),
        ("execution_mode", "HYBRID", "EXECUTION_MODE_NOT_LOCAL"),
        ("execution_mode", "UNKNOWN", "EXECUTION_MODE_NOT_LOCAL"),
        ("endpoint_class", "PRIVATE_NETWORK", "ENDPOINT_NOT_LOOPBACK"),
        ("endpoint_class", "PUBLIC_NETWORK", "ENDPOINT_NOT_LOOPBACK"),
        ("endpoint_class", "UNKNOWN", "ENDPOINT_NOT_LOOPBACK"),
        ("external_egress", "ALLOW", "EXTERNAL_EGRESS_NOT_DENIED"),
        ("external_egress", "UNKNOWN", "EXTERNAL_EGRESS_NOT_DENIED"),
        ("protected_state_route", "COMPRESS", "PROTECTED_STATE_NOT_BYPASSED"),
        ("protected_state_route", "UNKNOWN", "PROTECTED_STATE_NOT_BYPASSED"),
    )
    for field, value, code in cases:
        facts = ready_facts()
        facts[field] = value
        report = admission.evaluate_admission(request(facts))
        if report["decision"] != "SETUP_ALLOWED" or report["canary_ready"]:
            fail(f"{field}={value} incorrectly became ready: {report}")
        if code not in report["blockers"]:
            fail(f"{field}={value} missing blocker {code}: {report}")


def test_every_boolean_safety_fact_blocks_when_false_null_or_missing() -> None:
    for field, code in admission.BOOLEAN_REQUIREMENTS:
        for value in (False, None):
            facts = ready_facts()
            facts[field] = value
            report = admission.evaluate_admission(request(facts))
            if report["canary_ready"] or report["decision"] != "SETUP_ALLOWED":
                fail(f"{field}={value!r} incorrectly became ready: {report}")
            if code not in report["blockers"]:
                fail(f"{field}={value!r} missing blocker {code}: {report}")
        facts = ready_facts()
        del facts[field]
        report = admission.evaluate_admission(request(facts))
        if report["canary_ready"] or code not in report["blockers"]:
            fail(f"missing {field} did not fail closed: {report}")


def test_schema_rejects_content_and_unbounded_fields() -> None:
    for field in ("prompt", "source", "path", "credential", "tool_output"):
        doc = request()
        doc[field] = "forbidden"
        expect_error("ENVELOPE_INVALID", lambda doc=doc: admission.evaluate_admission(doc))

    doc = request()
    doc["runtime_facts"]["raw_prompt"] = "forbidden"
    expect_error("ENVELOPE_INVALID", lambda: admission.evaluate_admission(doc))

    doc = request()
    del doc["runtime_facts"]
    expect_error("ENVELOPE_INVALID", lambda: admission.evaluate_admission(doc))


def test_registry_is_source_pinned_bounded_and_strict() -> None:
    original = admission.REGISTRY_PATH
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "registry.json"

        def check(candidate: dict[str, object], expected: str = "REGISTRY_INVALID") -> None:
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "learned-compressor-candidate-registry",
                        "candidates": [candidate],
                    }
                ),
                encoding="utf-8",
            )
            admission.REGISTRY_PATH = path
            expect_error(expected, lambda: admission.evaluate_admission(request()))

        base = {
            **PIN,
            "integration_mode": "LOCAL_SELF_HOST",
        }
        for field, value in (
            ("candidate_id", "Bad Candidate"),
            ("source_repo", "mutable-main"),
            ("source_commit", "main"),
            ("package_version", "v 1"),
            ("license", "Apache 2"),
            ("integration_mode", "HOSTED"),
        ):
            candidate = copy.deepcopy(base)
            candidate[field] = value
            check(candidate)

        duplicate = {
            "schema_version": 1,
            "kind": "learned-compressor-candidate-registry",
            "candidates": [base, copy.deepcopy(base)],
        }
        path.write_text(json.dumps(duplicate), encoding="utf-8")
        admission.REGISTRY_PATH = path
        expect_error("REGISTRY_INVALID", lambda: admission.evaluate_admission(request()))
    admission.REGISTRY_PATH = original


def test_output_is_deterministic_bounded_and_non_authorizing() -> None:
    first = admission.evaluate_admission(request())
    second = admission.evaluate_admission(request())
    if first != second:
        fail("same admission facts produced nondeterministic output")
    encoded = json.dumps(first, sort_keys=True)
    for forbidden in (
        "winner",
        "ranking",
        "recommendation",
        "promotion_allowed",
        "deploy_allowed",
        "merge_allowed",
        "raw_prompt",
        "tool_output",
        "credential",
    ):
        if forbidden in encoded:
            fail(f"admission report leaked authority/content field: {forbidden}")
    if len(first["blockers"]) > len(admission.RUNTIME_REQUIREMENTS) + len(
        admission.BOOLEAN_REQUIREMENTS
    ):
        fail(f"blocker output is unexpectedly unbounded: {first}")


def test_cli_and_source_are_offline_provider_free() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "request.json"
        source.write_text(json.dumps(request()), encoding="utf-8")
        command = [sys.executable, str(TOOL), "--input", str(source)]
        first = subprocess.run(
            command, text=True, capture_output=True, check=False
        )
        second = subprocess.run(
            command, text=True, capture_output=True, check=False
        )
        if first.returncode != 0 or second.returncode != 0:
            fail(f"admission CLI failed: {first.stdout} {first.stderr}")
        if first.stdout != second.stdout:
            fail("admission CLI output was not deterministic")
        if json.loads(first.stdout)["decision"] != "CANARY_READY":
            fail(f"admission CLI emitted wrong decision: {first.stdout}")

    source_text = TOOL.read_text(encoding="utf-8")
    for forbidden in (
        "import subprocess",
        "import requests",
        "import urllib",
        "import socket",
        "openai",
        "anthropic",
        "ollama",
        "pip install",
        "http://",
        "https://",
    ):
        if forbidden in source_text:
            fail(f"admission helper gained forbidden dependency/action: {forbidden}")


def main() -> int:
    tests = [
        test_all_verified_local_facts_are_canary_ready,
        test_source_pin_and_license_fail_closed,
        test_setup_allowed_can_be_not_canary_ready,
        test_runtime_mode_boundaries_block_readiness,
        test_every_boolean_safety_fact_blocks_when_false_null_or_missing,
        test_schema_rejects_content_and_unbounded_fields,
        test_registry_is_source_pinned_bounded_and_strict,
        test_output_is_deterministic_bounded_and_non_authorizing,
        test_cli_and_source_are_offline_provider_free,
    ]
    for test in tests:
        test()
    print("CONTEXT_LEARNED_ADMISSION_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
