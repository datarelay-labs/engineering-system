#!/usr/bin/env python3
"""Verify a purpose-bound owner-runtime override; NEVER issue authorization.

The host-administered Engineering System trust anchor is the only authority.
An evidence file cannot choose keys, verifiers, repositories or approval rules.
This validates a signed claim's structure; it never attests persona execution or
authorizes product release. Issuance requires a separately trusted owner gate.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

ANCHOR = Path("/etc/engineering-system/skills-trust-anchor.pub")
OPENSSL = Path("/usr/bin/openssl")
MAX_RECEIPT_AGE_SECONDS = 3600
MAX_CLOCK_SKEW_SECONDS = 60

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,79}$")
RUNTIME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RECEIPT_KEYS = frozenset({
    "schema_version", "kind", "purpose", "issuer", "target_repo",
    "candidate_head", "contract_path", "contract_sha256", "gate", "run_id",
    "runtime", "owner_actor", "owner_authority", "owner_approval_ref",
    "receipt_id", "issued_at_unix", "expires_at_unix", "signature",
})


class OverrideError(ValueError):
    pass


def _trusted_file(path: Path, *, executable: bool = False) -> bool:
    try:
        st = path.lstat()
        parent = path.parent.lstat()
    except OSError:
        return False
    return bool(
        stat.S_ISREG(st.st_mode)
        and not stat.S_ISLNK(st.st_mode)
        and st.st_uid == parent.st_uid == 0
        and not ((st.st_mode | parent.st_mode) & 0o022)
        and (not executable or os.access(path, os.X_OK))
    )


def _trusted_anchor() -> Path | None:
    # No environment, evidence field, argument or repository-provided key path.
    if not _trusted_file(ANCHOR) or not _trusted_file(OPENSSL, executable=True):
        return None
    return ANCHOR


def _github_repository(origin: str) -> str | None:
    # Match the entire remote; do not accept credential-bearing or arbitrary hosts.
    for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if origin.startswith(prefix):
            suffix = origin[len(prefix):]
            if suffix.endswith(".git"):
                suffix = suffix[:-4]
            return suffix if REPO_RE.fullmatch(suffix) else None
    return None


def _receipt_error(code: str) -> OverrideError:
    return OverrideError("OWNER_RUNTIME_OVERRIDE_" + code)


def _canonical_bytes(receipt: dict[str, Any]) -> bytes:
    return json.dumps(
        {key: value for key, value in receipt.items() if key != "signature"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _signature_valid(anchor: Path, receipt: dict[str, Any]) -> bool:
    try:
        signature = base64.b64decode(receipt["signature"], validate=True)
    except (ValueError, binascii.Error, KeyError, TypeError):
        return False
    if len(signature) != 64:
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="user-gate-override-") as directory:
            root = Path(directory)
            body = root / "body"
            sig = root / "sig"
            body.write_bytes(_canonical_bytes(receipt))
            sig.write_bytes(signature)
            cp = subprocess.run(
                [
                    str(OPENSSL), "pkeyutl", "-verify", "-pubin",
                    "-inkey", str(anchor), "-rawin", "-in", str(body),
                    "-sigfile", str(sig),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                timeout=5,
            )
            return cp.returncode == 0
    except (OSError, subprocess.TimeoutExpired, TypeError, ValueError):
        return False


def verify_owner_runtime_override(
    receipt: Any, *, origin: str, candidate_head: str, contract_path: str,
    contract_sha256: str, gate: str, run_id: str, runtime: str,
) -> None:
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        raise _receipt_error("FORMAT_INVALID")
    if (
        type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1
        or receipt["kind"] != "owner-runtime-override-receipt"
        or receipt["purpose"] != "user-acceptance-runtime-override"
        or receipt["issuer"] != "engineering-system-trusted-owner-boundary"
        or receipt["owner_authority"] not in ("admin", "maintain", "write")
        or not isinstance(receipt["owner_actor"], str)
        or not ACTOR_RE.fullmatch(receipt["owner_actor"])
        or not isinstance(receipt["owner_approval_ref"], str)
        or not 1 <= len(receipt["owner_approval_ref"]) <= 256
        or any(ch in receipt["owner_approval_ref"] for ch in "\r\n\x00")
        or not isinstance(receipt["receipt_id"], str)
        or not 8 <= len(receipt["receipt_id"]) <= 128
        or not ID_RE.fullmatch(receipt["receipt_id"])
        or not isinstance(receipt["runtime"], str)
        or not RUNTIME_RE.fullmatch(receipt["runtime"])
        or not isinstance(receipt["candidate_head"], str)
        or not SHA_RE.fullmatch(receipt["candidate_head"])
        or not isinstance(receipt["contract_sha256"], str)
        or not DIGEST_RE.fullmatch(receipt["contract_sha256"])
    ):
        raise _receipt_error("FORMAT_INVALID")

    repo = _github_repository(origin)
    if repo is None:
        raise _receipt_error("REPOSITORY_UNVERIFIED")
    expected = {
        "target_repo": repo,
        "candidate_head": candidate_head,
        "contract_path": contract_path,
        "contract_sha256": contract_sha256,
        "gate": gate,
        "run_id": run_id,
        "runtime": runtime,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise _receipt_error("SCOPE_MISMATCH")

    issued = receipt.get("issued_at_unix")
    expires = receipt.get("expires_at_unix")
    if (
        type(issued) is not int or type(expires) is not int
        or expires <= issued or expires - issued > MAX_RECEIPT_AGE_SECONDS
    ):
        raise _receipt_error("TIME_INVALID")
    now = int(time.time())
    if issued > now + MAX_CLOCK_SKEW_SECONDS or expires <= now:
        raise _receipt_error("EXPIRED_OR_NOT_YET_VALID")

    anchor = _trusted_anchor()
    if anchor is None:
        raise _receipt_error("TRUST_UNAVAILABLE")
    if not _signature_valid(anchor, receipt):
        raise _receipt_error("SIGNATURE_INVALID")
