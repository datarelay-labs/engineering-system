#!/usr/bin/env python3
"""Test-only fixture helpers for skills-contract signed assertions.

Not a production/agent-callable authority mint. Not installed by adoption.
Trusted adapters/coordinators outside this repository own real signing.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

# Test fixtures sign with the same fixed host binary production verification uses.
OPENSSL = "/usr/bin/openssl"
HIGH_RISK_CLASSES = frozenset({"external_write", "production_write", "destructive"})


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    body = {key: payload[key] for key in sorted(payload) if key != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signed_payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()


def canonical_request_sha256(request_payload: Any) -> str:
    payload = request_payload if isinstance(request_payload, dict) else {}
    body = {key: payload[key] for key in sorted(payload)}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
    ).hexdigest()


def generate_keypair(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    private_key = directory / "ed25519.priv.pem"
    public_key = directory / "ed25519.pub.pem"
    subprocess.run(
        [OPENSSL, "genpkey", "-algorithm", "Ed25519", "-out", str(private_key)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    subprocess.run(
        [OPENSSL, "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    os.chmod(private_key, 0o600)
    os.chmod(public_key, 0o444)
    return private_key, public_key


def sign_payload(private_key: Path, payload: dict[str, Any]) -> dict[str, Any]:
    message = canonical_payload_bytes(payload)
    with tempfile.TemporaryDirectory() as tmp:
        msg = Path(tmp) / "msg"
        sig = Path(tmp) / "sig"
        msg.write_bytes(message)
        subprocess.run(
            [
                OPENSSL,
                "pkeyutl",
                "-sign",
                "-inkey",
                str(private_key),
                "-rawin",
                "-in",
                str(msg),
                "-out",
                str(sig),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        out = dict(payload)
        out["signature"] = base64.b64encode(sig.read_bytes()).decode("ascii")
        return out


def write_binding_assertion(
    path: Path,
    *,
    private_key: Path,
    public_key: Path,
    profile: str,
    policy_digest: str,
    authority_permission: str,
    approved_classes: Iterable[str] = (),
) -> Path:
    payload = {
        "profile": profile,
        "policy_digest": policy_digest,
        "authority_permission": authority_permission,
        "approved_classes": sorted(approved_classes),
        "public_key_sha256": sha256_file(public_key),
    }
    path.write_text(json.dumps(sign_payload(private_key, payload), sort_keys=True, indent=2) + "\n")
    return path


def write_dispatch_assertion(
    path: Path,
    *,
    private_key: Path,
    public_key: Path,
    tool_id: str,
    classes: Iterable[str],
    policy_digest: str,
    request_payload: dict[str, Any] | None = None,
    dispatch_id: str = "",
    expires_at_unix: int | None = None,
    binding_assertion: Path | None = None,
    worktree: Path | None = None,
    session_id: str = "",
) -> Path:
    if binding_assertion is None:
        candidates = sorted(path.parent.glob("*binding.json"))
        if len(candidates) != 1:
            raise AssertionError("fixture requires exactly one binding assertion")
        binding_assertion = candidates[0]
    binding_payload = json.loads(binding_assertion.read_text(encoding="utf-8"))
    class_list = sorted(classes)
    if worktree is None:
        for parent in (path.parent, *path.parents):
            candidate = parent / "repo"
            if candidate.is_dir():
                worktree = candidate
                break
    if worktree is None:
        worktree = Path(__file__).resolve().parents[1]

    request = request_payload or {}
    high_risk = bool(set(class_list) & HIGH_RISK_CLASSES)
    if high_risk and "scope" not in binding_payload:
        required = ("target_repo", "workstream", "branch", "subject_head", "intent_revision")
        missing = [key for key in required if key not in request]
        if missing:
            raise AssertionError("high-risk fixture request missing authority scope: " + ",".join(missing))
        chosen_session = session_id or (
            "fixture-" + canonical_request_sha256(request)[:24]
        )
        scope = {
            "target_repo": request["target_repo"],
            "worktree": str(worktree.resolve()),
            "workstream": request["workstream"],
            "branch": request["branch"],
            "subject_head": request["subject_head"],
            "intent_revision": request["intent_revision"],
            "session_id": chosen_session,
        }
        unsigned = {key: value for key, value in binding_payload.items() if key != "signature"}
        unsigned["scope"] = scope
        binding_payload = sign_payload(private_key, unsigned)
        binding_assertion.write_text(
            json.dumps(binding_payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    scope = binding_payload.get("scope") if isinstance(binding_payload, dict) else None

    payload: dict[str, Any] = {
        "tool_id": tool_id,
        "classes": class_list,
        "policy_digest": policy_digest,
        "binding_public_key_sha256": sha256_file(public_key),
        "binding_sha256": signed_payload_sha256(binding_payload),
        "worktree": str(worktree.resolve()),
        "request_sha256": canonical_request_sha256(request),
    }
    if high_risk:
        if not isinstance(scope, dict):
            raise AssertionError("high-risk fixture binding scope is missing")
        payload["session_id"] = scope["session_id"]
        payload["scope_sha256"] = canonical_request_sha256(scope)
    if dispatch_id:
        payload["dispatch_id"] = dispatch_id
    if expires_at_unix is not None:
        payload["expires_at_unix"] = expires_at_unix
    path.write_text(json.dumps(sign_payload(private_key, payload), sort_keys=True, indent=2) + "\n")
    return path
