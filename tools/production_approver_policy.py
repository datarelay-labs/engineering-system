#!/usr/bin/env python3
"""Validation for root-administered exact production approval records."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

POLICY_KIND = "trusted_production_approver_policy"
POLICY_VERSION = 1
MAX_POLICY_BYTES = 64 * 1024
MAX_APPROVALS = 64
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
WORKSTREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
DISPATCH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

class PolicyError(Exception):
    pass

def canonical_json_sha256(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def packet_sha256(body: str) -> str:
    if not isinstance(body, str):
        raise PolicyError("production approval packet body is invalid")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()

def _validated_token(value: Any, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PolicyError(f"production approval {label} is invalid")
    return value

def _approval(raw: Any) -> dict[str, Any]:
    required = {
        "repository", "issue_id", "workstream", "branch", "subject_head",
        "intent_revision", "session_id", "dispatch_id", "packet_sha256",
        "request_sha256", "approved_by",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise PolicyError("production approval fields are invalid")
    repository = raw.get("repository")
    issue_id = raw.get("issue_id")
    workstream = raw.get("workstream")
    branch = raw.get("branch")
    subject_head = str(raw.get("subject_head") or "").lower()
    intent_revision = raw.get("intent_revision")
    session_id = _validated_token(raw.get("session_id"), "session id", SESSION_ID_RE)
    dispatch_id = _validated_token(raw.get("dispatch_id"), "dispatch id", DISPATCH_ID_RE)
    packet_digest = str(raw.get("packet_sha256") or "").lower()
    request_digest = str(raw.get("request_sha256") or "").lower()
    approved_by = raw.get("approved_by")
    if not isinstance(repository, str) or REPOSITORY_RE.fullmatch(repository) is None:
        raise PolicyError("production approval repository identity is invalid")
    if isinstance(issue_id, bool) or not isinstance(issue_id, int) or issue_id < 1:
        raise PolicyError("production approval issue id is invalid")
    if not isinstance(workstream, str) or WORKSTREAM_RE.fullmatch(workstream) is None:
        raise PolicyError("production approval workstream is invalid")
    if not isinstance(branch, str) or not branch or len(branch) > 240:
        raise PolicyError("production approval branch is invalid")
    if any(ch in branch for ch in "\r\n"):
        raise PolicyError("production approval branch is invalid")
    if SHA40_RE.fullmatch(subject_head) is None:
        raise PolicyError("production approval subject head is invalid")
    if isinstance(intent_revision, bool) or not isinstance(intent_revision, int) or intent_revision < 1:
        raise PolicyError("production approval intent revision is invalid")
    if SHA256_RE.fullmatch(packet_digest) is None:
        raise PolicyError("production approval packet digest is invalid")
    if SHA256_RE.fullmatch(request_digest) is None:
        raise PolicyError("production approval request digest is invalid")
    if not isinstance(approved_by, str) or LOGIN_RE.fullmatch(approved_by) is None:
        raise PolicyError("production approval approver login is invalid")
    return {
        "repository": repository,
        "issue_id": issue_id,
        "workstream": workstream,
        "branch": branch,
        "subject_head": subject_head,
        "intent_revision": intent_revision,
        "session_id": session_id,
        "dispatch_id": dispatch_id,
        "packet_sha256": packet_digest,
        "request_sha256": request_digest,
        "approved_by": approved_by,
    }

def normalize_policy(payload: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "kind", "approvals"}:
        raise PolicyError("production approver policy fields are invalid")
    if payload.get("schema_version") != POLICY_VERSION or payload.get("kind") != POLICY_KIND:
        raise PolicyError("production approver policy identity is invalid")
    approvals = payload.get("approvals")
    if not isinstance(approvals, list) or not approvals or len(approvals) > MAX_APPROVALS:
        raise PolicyError("production approver policy approvals are invalid")
    normalized = tuple(_approval(item) for item in approvals)
    seen: set[tuple[object, ...]] = set()
    dispatch_ids: set[str] = set()
    for item in normalized:
        identity = (
            item["repository"], item["issue_id"], item["workstream"], item["branch"],
            item["subject_head"], item["intent_revision"], item["session_id"],
            item["dispatch_id"], item["packet_sha256"], item["request_sha256"],
        )
        if identity in seen:
            raise PolicyError("production approval is duplicated")
        if item["dispatch_id"] in dispatch_ids:
            raise PolicyError("production approval dispatch id is duplicated")
        seen.add(identity)
        dispatch_ids.add(item["dispatch_id"])
    return normalized

def canonical_policy_bytes(approvals: tuple[dict[str, Any], ...]) -> bytes:
    ordered = sorted(
        approvals,
        key=lambda item: (
            item["repository"], item["issue_id"], item["workstream"], item["dispatch_id"]
        ),
    )
    payload = {
        "schema_version": POLICY_VERSION,
        "kind": POLICY_KIND,
        "approvals": [dict(item) for item in ordered],
    }
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(raw) > MAX_POLICY_BYTES:
        raise PolicyError("canonical production approver policy is too large")
    return raw

def load_policy_bytes(raw: bytes) -> tuple[dict[str, Any], ...]:
    if not raw or len(raw) > MAX_POLICY_BYTES:
        raise PolicyError("production approver policy size is invalid")
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError("production approver policy JSON is invalid") from exc
    return normalize_policy(payload)

def load_policy_file(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PolicyError("production approver policy is unavailable") from exc
    return load_policy_bytes(raw)

def find_exact_approval(
    approvals: tuple[dict[str, Any], ...],
    *,
    repository: str,
    issue_id: int,
    workstream: str,
    branch: str,
    subject_head: str,
    intent_revision: int,
    session_id: str,
    dispatch_id: str,
    packet_digest: str,
    request_digest: str,
) -> dict[str, Any] | None:
    for item in approvals:
        if (
            item["repository"] == repository
            and item["issue_id"] == issue_id
            and item["workstream"] == workstream
            and item["branch"] == branch
            and item["subject_head"] == subject_head
            and item["intent_revision"] == intent_revision
            and item["session_id"] == session_id
            and item["dispatch_id"] == dispatch_id
            and item["packet_sha256"] == packet_digest
            and item["request_sha256"] == request_digest
        ):
            return item
    return None
