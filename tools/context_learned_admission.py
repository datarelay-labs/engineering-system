#!/usr/bin/env python3
"""Offline fail-closed admission gate for learned context-compressor canaries."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas/context-learned-canary-admission.schema.json"
REGISTRY_PATH = ROOT / "evals/context-optimization/learned-candidates.json"
CANDIDATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SOURCE_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PACKAGE_VERSION_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,40}$")
LICENSE_RE = re.compile(r"^[A-Za-z0-9.-]{1,40}$")
SOURCE_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
INTEGRATION_MODES = frozenset({"LOCAL_SELF_HOST"})
VERIFICATION_TOOL = ROOT / "tools/verification-contract.py"
TRUST_WORKSTREAM_PREFIX = "learned-canary-"
TRUST_EVIDENCE = (
    ("execution_mode", "runtime", "learned.execution-mode"),
    ("endpoint_class", "runtime", "learned.endpoint-class"),
    ("external_egress", "runtime", "learned.external-egress"),
    ("protected_state_route", "runtime", "learned.protected-state-route"),
    ("deterministic_bypass", "test", "learned.deterministic-bypass"),
    ("exact_original_recovery_verified", "test", "learned.exact-recovery"),
    ("identifier_preservation_verified", "test", "learned.identifier-preservation"),
    ("cache_behavior_verified", "test", "learned.cache-behavior"),
    ("provider_usage_capture_ready", "runtime", "learned.provider-usage-capture"),
    ("live_comparability_gate_available", "test", "learned.live-comparability"),
    ("shadow_equivalence_gate_available", "test", "learned.shadow-equivalence"),
)


class AdmissionError(ValueError):
    pass


def _load_json(path: Path, code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise AdmissionError(code) from exc

RUNTIME_REQUIREMENTS = (
    ("execution_mode", "LOCAL_ONLY", "EXECUTION_MODE_NOT_LOCAL"),
    ("endpoint_class", "LOOPBACK", "ENDPOINT_NOT_LOOPBACK"),
    ("external_egress", "DENY", "EXTERNAL_EGRESS_NOT_DENIED"),
    ("protected_state_route", "BYPASS", "PROTECTED_STATE_NOT_BYPASSED"),
)
BOOLEAN_REQUIREMENTS = (
    ("deterministic_bypass", "DETERMINISTIC_BYPASS_UNVERIFIED"),
    ("exact_original_recovery_verified", "EXACT_RECOVERY_UNVERIFIED"),
    ("identifier_preservation_verified", "IDENTIFIER_PRESERVATION_UNVERIFIED"),
    ("cache_behavior_verified", "CACHE_BEHAVIOR_UNVERIFIED"),
    ("provider_usage_capture_ready", "PROVIDER_USAGE_CAPTURE_NOT_READY"),
    ("live_comparability_gate_available", "LIVE_COMPARABILITY_GATE_UNAVAILABLE"),
    ("shadow_equivalence_gate_available", "SHADOW_EQUIVALENCE_GATE_UNAVAILABLE"),
)


def _validate_request(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AdmissionError("ENVELOPE_INVALID")
    schema = _load_json(SCHEMA_PATH, "SCHEMA_UNAVAILABLE")
    if any(True for _ in Draft202012Validator(schema).iter_errors(raw)):
        raise AdmissionError("ENVELOPE_INVALID")
    return raw


def _load_registry() -> dict[str, dict[str, Any]]:
    raw = _load_json(REGISTRY_PATH, "REGISTRY_UNAVAILABLE")
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != 1
        or raw.get("kind") != "learned-compressor-candidate-registry"
    ):
        raise AdmissionError("REGISTRY_INVALID")
    candidates = raw.get("candidates")
    if not isinstance(candidates, list) or not candidates or len(candidates) > 16:
        raise AdmissionError("REGISTRY_INVALID")
    indexed: dict[str, dict[str, Any]] = {}
    required = {
        "candidate_id",
        "source_repo",
        "source_commit",
        "package_version",
        "license",
        "integration_mode",
    }
    for item in candidates:
        if not isinstance(item, dict) or set(item) != required:
            raise AdmissionError("REGISTRY_INVALID")
        candidate_id = item["candidate_id"]
        if (
            not isinstance(candidate_id, str)
            or CANDIDATE_ID_RE.fullmatch(candidate_id) is None
            or candidate_id in indexed
        ):
            raise AdmissionError("REGISTRY_INVALID")
        if (
            not isinstance(item["source_repo"], str)
            or SOURCE_REPO_RE.fullmatch(item["source_repo"]) is None
        ):
            raise AdmissionError("REGISTRY_INVALID")
        if (
            not isinstance(item["source_commit"], str)
            or SOURCE_COMMIT_RE.fullmatch(item["source_commit"]) is None
        ):
            raise AdmissionError("REGISTRY_INVALID")
        if (
            not isinstance(item["package_version"], str)
            or PACKAGE_VERSION_RE.fullmatch(item["package_version"]) is None
        ):
            raise AdmissionError("REGISTRY_INVALID")
        if (
            not isinstance(item["license"], str)
            or LICENSE_RE.fullmatch(item["license"]) is None
        ):
            raise AdmissionError("REGISTRY_INVALID")
        if (
            not isinstance(item["integration_mode"], str)
            or item["integration_mode"] not in INTEGRATION_MODES
        ):
            raise AdmissionError("REGISTRY_INVALID")
        indexed[candidate_id] = item
    return indexed


def _candidate(request: dict[str, Any]) -> dict[str, Any]:
    candidate = _load_registry().get(request["candidate_id"])
    if candidate is None:
        raise AdmissionError("CANDIDATE_UNKNOWN")
    checks = (
        ("source_repo", "SOURCE_REPO_MISMATCH"),
        ("source_commit", "SOURCE_COMMIT_MISMATCH"),
        ("package_version", "PACKAGE_VERSION_MISMATCH"),
        ("license", "LICENSE_MISMATCH"),
    )
    for field, code in checks:
        if request[field] != candidate[field]:
            raise AdmissionError(code)
    return candidate


def _verification_module() -> Any:
    name = "verification_contract"
    expected_origin = VERIFICATION_TOOL.resolve()
    cached = sys.modules.get(name)
    if cached is not None:
        origin = getattr(cached, "__file__", None)
        try:
            cached_origin = Path(origin).resolve() if isinstance(origin, str) else None
        except OSError as exc:
            raise AdmissionError("TRUST_CONTRACT_UNAVAILABLE") from exc
        if (
            cached_origin != expected_origin
            or not hasattr(cached, "TrustedCoordinatorBoundary")
        ):
            raise AdmissionError("TRUST_CONTRACT_UNAVAILABLE")
        return cached
    spec = importlib.util.spec_from_file_location(name, VERIFICATION_TOOL)
    if spec is None or spec.loader is None:
        raise AdmissionError("TRUST_CONTRACT_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise AdmissionError("TRUST_CONTRACT_UNAVAILABLE") from exc
    return module


def _trust_workstream(candidate: dict[str, Any]) -> str:
    return TRUST_WORKSTREAM_PREFIX + candidate["candidate_id"]


def _runtime_subject(candidate: dict[str, Any], binding: dict[str, Any]) -> str:
    payload = {
        "candidate_id": candidate["candidate_id"],
        "source_repo": candidate["source_repo"],
        "source_commit": candidate["source_commit"],
        "package_version": candidate["package_version"],
        "artifact_digest": binding["artifact_digest"],
        "environment_id": binding["environment_id"],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "learned-canary:" + hashlib.sha256(encoded).hexdigest()


def _trusted_runtime_evidence(
    candidate: dict[str, Any],
    request: dict[str, Any],
    boundary: Any,
) -> tuple[bool, list[str]]:
    binding = request.get("runtime_binding")
    if not isinstance(binding, dict):
        return False, ["RUNTIME_BINDING_REQUIRED"]
    if boundary is None:
        return False, ["TRUST_BOUNDARY_REQUIRED"]
    verification = _verification_module()
    if type(boundary) is not verification.TrustedCoordinatorBoundary:
        return False, ["TRUST_BOUNDARY_UNTRUSTED"]
    payload = boundary.payload()
    try:
        verification.reject_untrusted(payload, "boundary")
        errors = verification.schema_errors(
            verification.load_schema(
                ROOT,
                verification.BOUNDARY_SCHEMA_REL,
            ),
            payload,
        )
    except Exception:
        return False, ["TRUST_BOUNDARY_INVALID"]
    if errors or not isinstance(payload, dict):
        return False, ["TRUST_BOUNDARY_INVALID"]
    if payload["target_repo"] != candidate["source_repo"]:
        return False, ["TRUST_REPO_MISMATCH"]
    if payload["workstream"] != _trust_workstream(candidate):
        return False, ["TRUST_WORKSTREAM_MISMATCH"]
    if payload["intent_revision"] != binding["evidence_revision"]:
        return False, ["TRUST_REVISION_MISMATCH"]
    if payload["subject_head"] != candidate["source_commit"]:
        return False, ["TRUST_SUBJECT_HEAD_MISMATCH"]
    if payload.get("runtime_subject") != _runtime_subject(candidate, binding):
        return False, ["TRUST_RUNTIME_SUBJECT_MISMATCH"]

    expected = {(authority, evidence_id) for _field, authority, evidence_id in TRUST_EVIDENCE}
    observed: dict[tuple[str, str], str] = {}
    for item in payload["evidence"]:
        if item["subject_head"] != payload["subject_head"]:
            return False, ["TRUST_EVIDENCE_STALE_HEAD"]
        if item["intent_revision"] != payload["intent_revision"]:
            return False, ["TRUST_EVIDENCE_STALE_REVISION"]
        key = (item["authority"], item["id"])
        if key in observed:
            return False, ["TRUST_EVIDENCE_DUPLICATE"]
        observed[key] = item["status"]
    if set(observed) - expected:
        return False, ["TRUST_EVIDENCE_UNKNOWN"]
    if expected - set(observed):
        return False, ["TRUST_EVIDENCE_MISSING"]
    if any(observed[key] != "PASS" for key in expected):
        return False, ["TRUST_EVIDENCE_NOT_PASS"]
    return True, []


def _requirements(runtime: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    results: dict[str, bool] = {}
    blockers: list[str] = []
    for field, expected, code in RUNTIME_REQUIREMENTS:
        passed = runtime.get(field) == expected
        results[field] = passed
        if not passed:
            blockers.append(code)
    for field, code in BOOLEAN_REQUIREMENTS:
        passed = runtime.get(field) is True
        results[field] = passed
        if not passed:
            blockers.append(code)
    return results, blockers


def evaluate_admission(raw: Any, boundary: Any = None) -> dict[str, Any]:
    request = _validate_request(raw)
    candidate = _candidate(request)
    requirements, blockers = _requirements(request["runtime_facts"])
    trusted, trust_blockers = _trusted_runtime_evidence(
        candidate,
        request,
        boundary,
    )
    requirements["trusted_runtime_evidence"] = trusted
    blockers.extend(trust_blockers)
    canary_ready = not blockers
    return {
        "schema_version": 1,
        "kind": "context-learned-canary-admission-report",
        "decision": "CANARY_READY" if canary_ready else "SETUP_ALLOWED",
        "setup_allowed": True,
        "canary_ready": canary_ready,
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "source_repo": candidate["source_repo"],
            "source_commit": candidate["source_commit"],
            "package_version": candidate["package_version"],
            "license": candidate["license"],
            "integration_mode": candidate["integration_mode"],
        },
        "requirements": requirements,
        "blockers": blockers,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate source-pinned learned-compressor canary admission"
    )
    parser.add_argument("--input", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = evaluate_admission(
            _load_json(Path(args.input), "INPUT_INVALID")
        )
        json.dump(report, sys.stdout, sort_keys=True, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    except AdmissionError as exc:
        print(f"LEARNED_COMPRESSOR_ADMISSION=BLOCK reason={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
