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


def runtime_binding() -> dict[str, object]:
    return {
        "environment_id": "dev-atlas-canary",
        "artifact_digest": "a" * 64,
        "evidence_revision": 1,
    }


def request(
    runtime_facts: dict[str, object] | None = None,
    *,
    include_binding: bool = True,
) -> dict[str, object]:
    doc = {
        "schema_version": 1,
        "kind": "context-learned-canary-admission",
        **PIN,
        "runtime_facts": ready_facts() if runtime_facts is None else runtime_facts,
    }
    if include_binding:
        doc["runtime_binding"] = runtime_binding()
    return doc


def candidate_record() -> dict[str, object]:
    return {**PIN, "integration_mode": "LOCAL_SELF_HOST"}


def boundary_payload(doc: dict[str, object]) -> dict[str, object]:
    candidate = candidate_record()
    binding = doc["runtime_binding"]
    return {
        "schema_version": 1,
        "kind": "trust-evidence-boundary",
        "provenance": "coordinator-boundary",
        "target_repo": PIN["source_repo"],
        "workstream": admission._trust_workstream(candidate),
        "intent_revision": binding["evidence_revision"],
        "subject_head": PIN["source_commit"],
        "runtime_subject": admission._runtime_subject(candidate, binding),
        "evidence": [
            {
                "authority": authority,
                "id": evidence_id,
                "status": "PASS",
                "subject_head": PIN["source_commit"],
                "intent_revision": binding["evidence_revision"],
            }
            for _field, authority, evidence_id in admission.TRUST_EVIDENCE
        ],
    }


def trusted_boundary(doc: dict[str, object]):
    verification = admission._verification_module()
    return verification.TrustedCoordinatorBoundary(boundary_payload(doc))


def test_all_verified_local_facts_are_canary_ready() -> None:
    doc = request()
    report = admission.evaluate_admission(doc, trusted_boundary(doc))
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
    doc = request({})
    report = admission.evaluate_admission(doc, trusted_boundary(doc))
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
    runtime_fields = [
        field for field, _expected, _code in admission.RUNTIME_REQUIREMENTS
    ] + [field for field, _code in admission.BOOLEAN_REQUIREMENTS]
    if any(report["requirements"][field] for field in runtime_fields):
        fail(f"empty runtime facts unexpectedly passed requirements: {report}")
    if report["requirements"]["trusted_runtime_evidence"] is not True:
        fail(f"trusted evidence unexpectedly failed: {report}")


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
        doc = request(facts)
        report = admission.evaluate_admission(doc, trusted_boundary(doc))
        if report["decision"] != "SETUP_ALLOWED" or report["canary_ready"]:
            fail(f"{field}={value} incorrectly became ready: {report}")
        if code not in report["blockers"]:
            fail(f"{field}={value} missing blocker {code}: {report}")


def test_every_boolean_safety_fact_blocks_when_false_null_or_missing() -> None:
    for field, code in admission.BOOLEAN_REQUIREMENTS:
        for value in (False, None):
            facts = ready_facts()
            facts[field] = value
            doc = request(facts)
            report = admission.evaluate_admission(doc, trusted_boundary(doc))
            if report["canary_ready"] or report["decision"] != "SETUP_ALLOWED":
                fail(f"{field}={value!r} incorrectly became ready: {report}")
            if code not in report["blockers"]:
                fail(f"{field}={value!r} missing blocker {code}: {report}")
        facts = ready_facts()
        del facts[field]
        doc = request(facts)
        report = admission.evaluate_admission(doc, trusted_boundary(doc))
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


def test_trust_boundary_uses_canonical_verification_module() -> None:
    verification = admission._verification_module()
    if verification.__name__ != "verification_contract":
        fail(f"verification module alias drifted: {verification.__name__}")
    if sys.modules.get("verification_contract") is not verification:
        fail("canonical verification module identity is not shared")
    doc = request()
    boundary = verification.TrustedCoordinatorBoundary(boundary_payload(doc))
    report = admission.evaluate_admission(doc, boundary)
    if report["decision"] != "CANARY_READY":
        fail(f"canonical verification boundary was rejected: {report}")


def test_verification_module_rejects_preloaded_wrong_origin() -> None:
    original = sys.modules.get("verification_contract")
    fake = type(
        "FakeVerificationModule",
        (),
        {
            "__file__": "/tmp/fake-verification-contract.py",
            "TrustedCoordinatorBoundary": object,
        },
    )()
    sys.modules["verification_contract"] = fake
    try:
        expect_error("TRUST_CONTRACT_UNAVAILABLE", admission._verification_module)
    finally:
        if original is None:
            sys.modules.pop("verification_contract", None)
        else:
            sys.modules["verification_contract"] = original


def test_trust_boundary_is_required_and_raw_dict_is_untrusted() -> None:
    doc = request()
    report = admission.evaluate_admission(doc)
    if report["decision"] != "SETUP_ALLOWED" or report["canary_ready"]:
        fail(f"caller facts minted readiness without trust boundary: {report}")
    if report["blockers"] != ["TRUST_BOUNDARY_REQUIRED"]:
        fail(f"missing trust boundary blocker drifted: {report}")

    report = admission.evaluate_admission(doc, boundary_payload(doc))
    if report["decision"] != "SETUP_ALLOWED" or report["canary_ready"]:
        fail(f"raw dict minted trusted readiness: {report}")
    if report["blockers"] != ["TRUST_BOUNDARY_UNTRUSTED"]:
        fail(f"raw trust boundary blocker drifted: {report}")

    no_binding = request(include_binding=False)
    report = admission.evaluate_admission(no_binding)
    if report["blockers"] != ["RUNTIME_BINDING_REQUIRED"]:
        fail(f"missing runtime binding blocker drifted: {report}")


def test_trust_boundary_binds_artifact_environment_and_revision() -> None:
    cases = (
        ("target_repo", "other/project", "TRUST_REPO_MISMATCH"),
        ("workstream", "learned-canary-other", "TRUST_WORKSTREAM_MISMATCH"),
        ("intent_revision", 2, "TRUST_REVISION_MISMATCH"),
        ("subject_head", "b" * 40, "TRUST_SUBJECT_HEAD_MISMATCH"),
        ("runtime_subject", "learned-canary:" + "b" * 64, "TRUST_RUNTIME_SUBJECT_MISMATCH"),
    )
    for field, value, code in cases:
        doc = request()
        payload = boundary_payload(doc)
        payload[field] = value
        verification = admission._verification_module()
        boundary = verification.TrustedCoordinatorBoundary(payload)
        report = admission.evaluate_admission(doc, boundary)
        if report["canary_ready"] or code not in report["blockers"]:
            fail(f"{field} mismatch did not block readiness: {report}")

    doc = request()
    boundary = trusted_boundary(doc)
    doc["runtime_binding"]["artifact_digest"] = "b" * 64
    report = admission.evaluate_admission(doc, boundary)
    if "TRUST_RUNTIME_SUBJECT_MISMATCH" not in report["blockers"]:
        fail(f"stale artifact evidence was accepted: {report}")

    doc = request()
    boundary = trusted_boundary(doc)
    doc["runtime_binding"]["environment_id"] = "other-host"
    report = admission.evaluate_admission(doc, boundary)
    if "TRUST_RUNTIME_SUBJECT_MISMATCH" not in report["blockers"]:
        fail(f"stale environment evidence was accepted: {report}")


def test_trust_evidence_set_fails_closed() -> None:
    verification = admission._verification_module()

    doc = request()
    payload = boundary_payload(doc)
    payload["evidence"].pop()
    report = admission.evaluate_admission(
        doc, verification.TrustedCoordinatorBoundary(payload)
    )
    if "TRUST_EVIDENCE_MISSING" not in report["blockers"]:
        fail(f"missing trust evidence was accepted: {report}")

    doc = request()
    payload = boundary_payload(doc)
    payload["evidence"][0]["status"] = "UNKNOWN"
    report = admission.evaluate_admission(
        doc, verification.TrustedCoordinatorBoundary(payload)
    )
    if "TRUST_EVIDENCE_NOT_PASS" not in report["blockers"]:
        fail(f"UNKNOWN trust evidence was accepted: {report}")

    doc = request()
    payload = boundary_payload(doc)
    payload["evidence"].append(copy.deepcopy(payload["evidence"][0]))
    report = admission.evaluate_admission(
        doc, verification.TrustedCoordinatorBoundary(payload)
    )
    if "TRUST_EVIDENCE_DUPLICATE" not in report["blockers"]:
        fail(f"duplicate trust evidence was accepted: {report}")

    doc = request()
    payload = boundary_payload(doc)
    payload["evidence"].append(
        {
            "authority": "runtime",
            "id": "learned.unknown",
            "status": "PASS",
            "subject_head": PIN["source_commit"],
            "intent_revision": doc["runtime_binding"]["evidence_revision"],
        }
    )
    report = admission.evaluate_admission(
        doc, verification.TrustedCoordinatorBoundary(payload)
    )
    if "TRUST_EVIDENCE_UNKNOWN" not in report["blockers"]:
        fail(f"unknown trust evidence was accepted: {report}")


def test_output_is_deterministic_bounded_and_non_authorizing() -> None:
    doc = request()
    boundary = trusted_boundary(doc)
    first = admission.evaluate_admission(doc, boundary)
    second = admission.evaluate_admission(doc, boundary)
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
        cli_report = json.loads(first.stdout)
        if cli_report["decision"] != "SETUP_ALLOWED" or cli_report["canary_ready"]:
            fail(f"CLI minted trusted readiness: {first.stdout}")
        if cli_report["blockers"] != ["TRUST_BOUNDARY_REQUIRED"]:
            fail(f"CLI trust blocker drifted: {first.stdout}")

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
        test_trust_boundary_uses_canonical_verification_module,
        test_verification_module_rejects_preloaded_wrong_origin,
        test_trust_boundary_is_required_and_raw_dict_is_untrusted,
        test_trust_boundary_binds_artifact_environment_and_revision,
        test_trust_evidence_set_fails_closed,
        test_output_is_deterministic_bounded_and_non_authorizing,
        test_cli_and_source_are_offline_provider_free,
    ]
    for test in tests:
        test()
    print("CONTEXT_LEARNED_ADMISSION_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
