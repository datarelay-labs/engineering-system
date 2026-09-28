#!/usr/bin/env python3
"""Offline assessor for bounded learned-compressor local runtime evidence.

This module does not launch containers, models, proxies, providers, or network
requests. It validates caller-supplied bounded observations against the
source-pinned learned-candidate registry and emits content-free evidence only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas/context-learned-runtime-evidence.schema.json"
ADMISSION_TOOL = ROOT / "tools/context_learned_admission.py"


class RuntimeEvidenceError(ValueError):
    pass


def _load_json(path: Path, code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise RuntimeEvidenceError(code) from exc


def _admission_module() -> Any:
    name = "context_learned_admission_runtime_dependency"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, ADMISSION_TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeEvidenceError("ADMISSION_CONTRACT_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise RuntimeEvidenceError("ADMISSION_CONTRACT_UNAVAILABLE") from exc
    return module


def _validate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeEvidenceError("ENVELOPE_INVALID")
    schema = _load_json(SCHEMA_PATH, "SCHEMA_UNAVAILABLE")
    if any(True for _ in Draft202012Validator(schema).iter_errors(raw)):
        raise RuntimeEvidenceError("ENVELOPE_INVALID")
    return raw


def _current_system_head() -> str:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "--no-replace-objects", "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    head = completed.stdout.strip()
    if completed.returncode != 0 or len(head) != 40:
        raise RuntimeEvidenceError("SYSTEM_HEAD_UNAVAILABLE")
    return head


def _repository_clean() -> bool:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "--no-replace-objects",
            "status",
            "--porcelain",
            "--untracked-files=all",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeEvidenceError("REPOSITORY_STATE_UNAVAILABLE")
    return not completed.stdout.strip()


def _candidate(request: dict[str, Any]) -> dict[str, Any]:
    admission = _admission_module()
    try:
        registry = admission._load_registry()
    except Exception as exc:
        raise RuntimeEvidenceError("CANDIDATE_REGISTRY_UNAVAILABLE") from exc
    candidate = registry.get(request["candidate"]["candidate_id"])
    if candidate is None:
        raise RuntimeEvidenceError("CANDIDATE_UNKNOWN")
    for field in (
        "source_repo",
        "source_commit",
        "package_version",
        "license",
        "integration_mode",
    ):
        if request["candidate"][field] != candidate[field]:
            raise RuntimeEvidenceError(f"CANDIDATE_{field.upper()}_MISMATCH")
    return candidate


def _sandbox_requirements(request: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    sandbox = request["sandbox"]
    backend = request["backend"]
    requirements = {
        "docker_runtime": sandbox["container_runtime"] == "DOCKER",
        "network_none": sandbox["network_mode"] == "NONE",
        "loopback_bind": sandbox["loopback_bind_ok"] is True,
        "external_connect_blocked": sandbox["external_connect_blocked"] is True,
        "backend_endpoint_loopback": sandbox["backend_endpoint_class"] == "LOOPBACK",
        "local_compression_backend": backend["use_gpu_server"] is False,
    }
    codes = {
        "docker_runtime": "CONTAINER_RUNTIME_NOT_DOCKER",
        "network_none": "NETWORK_MODE_NOT_NONE",
        "loopback_bind": "LOOPBACK_BIND_UNVERIFIED",
        "external_connect_blocked": "EXTERNAL_CONNECT_NOT_BLOCKED",
        "backend_endpoint_loopback": "BACKEND_ENDPOINT_NOT_LOOPBACK",
        "local_compression_backend": "HOSTED_COMPRESSION_BACKEND",
    }
    blockers = [codes[key] for key, passed in requirements.items() if not passed]
    return requirements, blockers


def _model_requirements(request: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    backend = request["backend"]
    model_requested = any(
        backend.get(field) is not None
        for field in (
            "model_available",
            "compression_probe_passed",
            "probe_input_sha256",
            "probe_output_sha256",
            "probe_input_tokens",
            "probe_output_tokens",
        )
    )
    if not model_requested:
        return {
            "model_available": False,
            "compression_probe": False,
            "probe_digest_bound": False,
            "probe_token_counts_bound": False,
        }, ["MODEL_RUNTIME_NOT_PROVEN"]

    digest_bound = (
        isinstance(backend.get("probe_input_sha256"), str)
        and isinstance(backend.get("probe_output_sha256"), str)
    )
    token_counts_bound = (
        isinstance(backend.get("probe_input_tokens"), int)
        and isinstance(backend.get("probe_output_tokens"), int)
        and backend["probe_input_tokens"] > 0
        and backend["probe_output_tokens"] >= 0
    )
    requirements = {
        "model_available": backend.get("model_available") is True,
        "compression_probe": backend.get("compression_probe_passed") is True,
        "probe_digest_bound": digest_bound,
        "probe_token_counts_bound": token_counts_bound,
    }
    codes = {
        "model_available": "MODEL_NOT_AVAILABLE",
        "compression_probe": "COMPRESSION_PROBE_NOT_PASS",
        "probe_digest_bound": "PROBE_DIGESTS_MISSING",
        "probe_token_counts_bound": "PROBE_TOKEN_COUNTS_MISSING",
    }
    blockers = [codes[key] for key, passed in requirements.items() if not passed]
    return requirements, blockers


def assess(raw: Any) -> dict[str, Any]:
    request = _validate(raw)
    if request["system_head"] != _current_system_head():
        raise RuntimeEvidenceError("SYSTEM_HEAD_MISMATCH")
    candidate = _candidate(request)
    sandbox_requirements, sandbox_blockers = _sandbox_requirements(request)
    model_requirements, model_blockers = _model_requirements(request)

    sandbox_qualified = not sandbox_blockers
    model_qualified = sandbox_qualified and not model_blockers
    if model_qualified:
        decision = "LOCAL_MODEL_QUALIFIED"
    elif sandbox_qualified:
        decision = "SANDBOX_QUALIFIED"
    else:
        decision = "EVIDENCE_ONLY"

    return {
        "schema_version": 1,
        "kind": "context-learned-runtime-evidence-report",
        "decision": decision,
        "authority": "EVIDENCE_ONLY",
        "system_head": request["system_head"],
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "source_repo": candidate["source_repo"],
            "source_commit": candidate["source_commit"],
            "package_version": candidate["package_version"],
            "license": candidate["license"],
            "integration_mode": candidate["integration_mode"],
        },
        "artifact_digest": request["artifact_digest"],
        "environment_id": request["environment_id"],
        "evidence_revision": request["evidence_revision"],
        "sandbox_qualified": sandbox_qualified,
        "model_qualified": model_qualified,
        "requirements": {
            **sandbox_requirements,
            **model_requirements,
        },
        "probe": {
            "input_sha256": request["backend"].get("probe_input_sha256"),
            "output_sha256": request["backend"].get("probe_output_sha256"),
            "input_tokens": request["backend"].get("probe_input_tokens"),
            "output_tokens": request["backend"].get("probe_output_tokens"),
        },
        "blockers": sandbox_blockers + model_blockers,
        "canary_ready": False,
        "provider_call_authority": "NONE",
        "promotion_authority": "NONE",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assess bounded learned-compressor local runtime evidence"
    )
    parser.add_argument("--input", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not _repository_clean():
            raise RuntimeEvidenceError("REPOSITORY_DIRTY")
        report = assess(_load_json(Path(args.input), "INPUT_INVALID"))
        json.dump(report, sys.stdout, sort_keys=True, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    except RuntimeEvidenceError as exc:
        print(f"LEARNED_RUNTIME_EVIDENCE=BLOCK reason={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
