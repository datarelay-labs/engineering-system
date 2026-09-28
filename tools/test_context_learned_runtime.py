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
TOOL = ROOT / "tools/context_learned_runtime.py"

spec = importlib.util.spec_from_file_location("learned_runtime_tested", TOOL)
assert spec and spec.loader
runtime = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runtime
spec.loader.exec_module(runtime)

PIN = {
    "candidate_id": "paritok-local",
    "source_repo": "Paritok-official/paritok-4b-v1",
    "source_commit": "2c913302073367f2402d8bc4bb1a929a3f70a030",
    "package_version": "1.3.13",
    "license": "Apache-2.0",
    "integration_mode": "LOCAL_SELF_HOST",
}
HEAD = subprocess.run(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
    text=True,
    stdout=subprocess.PIPE,
    check=True,
).stdout.strip()


def fail(message: str) -> None:
    raise AssertionError(message)


def expect_error(code: str, callback) -> None:
    try:
        callback()
    except runtime.RuntimeEvidenceError as exc:
        if str(exc) != code:
            fail(f"expected {code}, got {exc}")
    else:
        fail(f"expected failure {code}")


def observation(*, with_model: bool = False) -> dict[str, object]:
    backend: dict[str, object] = {"use_gpu_server": False}
    if with_model:
        backend.update(
            {
                "model_available": True,
                "compression_probe_passed": True,
                "probe_input_sha256": "a" * 64,
                "probe_output_sha256": "b" * 64,
                "probe_input_tokens": 1200,
                "probe_output_tokens": 300,
            }
        )
    return {
        "schema_version": 1,
        "kind": "context-learned-runtime-evidence",
        "system_head": HEAD,
        "candidate": copy.deepcopy(PIN),
        "artifact_digest": "c" * 64,
        "environment_id": "dev-drcontrol-docker-none",
        "evidence_revision": 1,
        "sandbox": {
            "container_runtime": "DOCKER",
            "network_mode": "NONE",
            "loopback_bind_ok": True,
            "external_connect_blocked": True,
            "backend_endpoint_class": "LOOPBACK",
        },
        "backend": backend,
    }


def test_valid_sandbox_is_qualified_but_not_model_or_canary_ready() -> None:
    report = runtime.assess(observation())
    if report["decision"] != "SANDBOX_QUALIFIED":
        fail(f"sandbox did not qualify: {report}")
    if not report["sandbox_qualified"] or report["model_qualified"]:
        fail(f"sandbox/model flags drifted: {report}")
    if report["canary_ready"]:
        fail(f"runtime evidence minted canary readiness: {report}")
    if report["authority"] != "EVIDENCE_ONLY":
        fail(f"runtime evidence gained authority: {report}")
    if report["provider_call_authority"] != "NONE":
        fail(f"runtime evidence gained provider-call authority: {report}")
    if report["blockers"] != ["MODEL_RUNTIME_NOT_PROVEN"]:
        fail(f"sandbox-only blocker drifted: {report}")


def test_valid_local_model_probe_is_qualified_and_content_free() -> None:
    report = runtime.assess(observation(with_model=True))
    if report["decision"] != "LOCAL_MODEL_QUALIFIED":
        fail(f"local model proof did not qualify: {report}")
    if not report["sandbox_qualified"] or not report["model_qualified"]:
        fail(f"local model flags drifted: {report}")
    if report["probe"] != {
        "input_sha256": "a" * 64,
        "output_sha256": "b" * 64,
        "input_tokens": 1200,
        "output_tokens": 300,
    }:
        fail(f"bounded probe binding drifted: {report['probe']}")


def test_system_head_is_exact_and_fail_closed() -> None:
    doc = observation()
    doc["system_head"] = "0" * 40
    expect_error("SYSTEM_HEAD_MISMATCH", lambda: runtime.assess(doc))


def test_candidate_identity_reuses_existing_registry() -> None:
    cases = (
        ("source_repo", "other/project", "CANDIDATE_SOURCE_REPO_MISMATCH"),
        ("source_commit", "0" * 40, "CANDIDATE_SOURCE_COMMIT_MISMATCH"),
        ("package_version", "9.9.9", "CANDIDATE_PACKAGE_VERSION_MISMATCH"),
        ("license", "MIT", "CANDIDATE_LICENSE_MISMATCH"),
    )
    for field, value, code in cases:
        doc = observation()
        doc["candidate"][field] = value
        expect_error(code, lambda doc=doc: runtime.assess(doc))

    doc = observation()
    doc["candidate"]["candidate_id"] = "missing"
    expect_error("CANDIDATE_UNKNOWN", lambda: runtime.assess(doc))


def test_sandbox_boundaries_fail_closed() -> None:
    cases = (
        ("container_runtime", "OTHER", "CONTAINER_RUNTIME_NOT_DOCKER"),
        ("network_mode", "BRIDGE", "NETWORK_MODE_NOT_NONE"),
        ("network_mode", "HOST", "NETWORK_MODE_NOT_NONE"),
        ("loopback_bind_ok", False, "LOOPBACK_BIND_UNVERIFIED"),
        ("loopback_bind_ok", None, "LOOPBACK_BIND_UNVERIFIED"),
        ("external_connect_blocked", False, "EXTERNAL_CONNECT_NOT_BLOCKED"),
        ("external_connect_blocked", None, "EXTERNAL_CONNECT_NOT_BLOCKED"),
        ("backend_endpoint_class", "PRIVATE_NETWORK", "BACKEND_ENDPOINT_NOT_LOOPBACK"),
    )
    for field, value, code in cases:
        doc = observation()
        doc["sandbox"][field] = value
        report = runtime.assess(doc)
        if report["decision"] != "EVIDENCE_ONLY" or report["sandbox_qualified"]:
            fail(f"{field}={value!r} incorrectly qualified: {report}")
        if code not in report["blockers"]:
            fail(f"{field}={value!r} missing blocker {code}: {report}")

    doc = observation()
    doc["backend"]["use_gpu_server"] = True
    report = runtime.assess(doc)
    if report["sandbox_qualified"] or "HOSTED_COMPRESSION_BACKEND" not in report["blockers"]:
        fail(f"hosted backend incorrectly qualified: {report}")


def test_partial_model_probe_does_not_qualify() -> None:
    doc = observation()
    doc["backend"]["model_available"] = True
    report = runtime.assess(doc)
    if report["model_qualified"]:
        fail(f"partial model evidence qualified: {report}")
    expected = {
        "COMPRESSION_PROBE_NOT_PASS",
        "PROBE_DIGESTS_MISSING",
        "PROBE_TOKEN_COUNTS_MISSING",
    }
    if not expected.issubset(set(report["blockers"])):
        fail(f"partial model blockers drifted: {report}")


def test_schema_rejects_provider_content_and_freeform_fields() -> None:
    for field in (
        "provider_url",
        "provider_key",
        "prompt",
        "source",
        "raw_model_output",
        "absolute_path",
        "credential",
    ):
        doc = observation()
        doc[field] = "forbidden"
        expect_error("ENVELOPE_INVALID", lambda doc=doc: runtime.assess(doc))

    doc = observation()
    doc["sandbox"]["provider_url"] = "https://example.invalid"
    expect_error("ENVELOPE_INVALID", lambda: runtime.assess(doc))

    doc = observation()
    doc["backend"]["raw_output"] = "secret"
    expect_error("ENVELOPE_INVALID", lambda: runtime.assess(doc))


def test_output_is_deterministic_bounded_and_non_authorizing() -> None:
    doc = observation(with_model=True)
    first = runtime.assess(doc)
    second = runtime.assess(doc)
    if first != second:
        fail("same runtime evidence produced nondeterministic output")
    encoded = json.dumps(first, sort_keys=True)
    for forbidden in (
        "winner",
        "ranking",
        "recommendation",
        "merge_allowed",
        "deploy_allowed",
        "promotion_allowed",
        "provider_url",
        "credential",
        "raw_model_output",
    ):
        if forbidden in encoded:
            fail(f"runtime evidence leaked forbidden field {forbidden}")
    if len(first["blockers"]) > 10:
        fail(f"runtime evidence blockers unexpectedly unbounded: {first}")


def test_cli_is_offline_and_fails_closed_on_bad_input() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "observation.json"
        source.write_text(json.dumps(observation()), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(TOOL), "--input", str(source)],
            text=True,
            capture_output=True,
            check=False,
        )
        if runtime._repository_clean():
            if completed.returncode != 0:
                fail(f"runtime CLI failed on clean repository: {completed.stderr}")
            report = json.loads(completed.stdout)
            if report["decision"] != "SANDBOX_QUALIFIED":
                fail(f"runtime CLI decision drifted: {report}")
        else:
            if completed.returncode != 2 or "REPOSITORY_DIRTY" not in completed.stderr:
                fail(
                    "runtime CLI did not fail closed on dirty repository: "
                    f"{completed.stdout} {completed.stderr}"
                )

        source.write_text("{}", encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(TOOL), "--input", str(source)],
            text=True,
            capture_output=True,
            check=False,
        )
        if runtime._repository_clean():
            expected = "ENVELOPE_INVALID"
        else:
            expected = "REPOSITORY_DIRTY"
        if completed.returncode != 2 or expected not in completed.stderr:
            fail(f"runtime CLI did not fail closed: {completed.stdout} {completed.stderr}")

    source_text = TOOL.read_text(encoding="utf-8")
    for forbidden in (
        "docker run",
        "docker exec",
        "openai",
        "anthropic",
        "ollama pull",
        "http://",
        "https://api.",
    ):
        if forbidden in source_text:
            fail(f"runtime assessor gained forbidden execution/provider path: {forbidden}")


def main() -> int:
    tests = [
        test_valid_sandbox_is_qualified_but_not_model_or_canary_ready,
        test_valid_local_model_probe_is_qualified_and_content_free,
        test_system_head_is_exact_and_fail_closed,
        test_candidate_identity_reuses_existing_registry,
        test_sandbox_boundaries_fail_closed,
        test_partial_model_probe_does_not_qualify,
        test_schema_rejects_provider_content_and_freeform_fields,
        test_output_is_deterministic_bounded_and_non_authorizing,
        test_cli_is_offline_and_fails_closed_on_bad_input,
    ]
    for test in tests:
        test()
    print("CONTEXT_LEARNED_RUNTIME_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
