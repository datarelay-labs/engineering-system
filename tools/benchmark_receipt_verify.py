#!/usr/bin/python3
"""Fixed root-owned benchmark receipt authority.

The terminal decision is this program installed at
``/usr/lib/engineering-system/benchmark-receipt-verify``. Repository Python
may prepare an unsigned body. It cannot mint the host signature or select
the trust path. This module does not import repository verifier code.

``decide`` exits 0 only when the fixed anchor verifies the signature, the
repository HEAD matches the signed system head, the committed terminal
blobs match a clean worktree, and the installed helper bytes equal
``HEAD:tools/benchmark_receipt_verify.py``. ``sign`` writes a receipt only
when this process is that installed helper and the effective uid is root.
Any other invocation fails closed and writes nothing.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

HELPER_PATH = "/usr/lib/engineering-system/benchmark-receipt-verify"
HELPER_SOURCE = "tools/benchmark_receipt_verify.py"
RECEIPT_KIND = "benchmark-host-receipt-v2"
ANCHOR_PATH = "/etc/engineering-system/skills-trust-anchor.pub"
PRIVATE_KEY_PATH = "/etc/engineering-system/skills-trust-anchor.key"
RECEIPT_DIR = "/var/lib/engineering-system/benchmark-receipts"
OPENSSL_PATH = "/usr/bin/openssl"
GIT_PATH = "/usr/bin/git"
TERMINAL_PATHS = (
    "tools/benchmark_execution.py",
    "tools/benchmark_receipt_verify.py",
    "tools/efficiency_telemetry.py",
    "tools/persistent_benchmark_telemetry.py",
    "tools/terminal_code_identity.py",
)
LANES = frozenset({"CONTROL", "CANDIDATE"})
RUN_RE = re.compile(r"^[a-f0-9]{32}$")
HEAD_RE = re.compile(r"^[a-f0-9]{40}$")
MAX_ASSERTION_BYTES = 65536
Provenance = Callable[..., bool]


@dataclass(frozen=True)
class HostBoundary:
    helper: str = HELPER_PATH
    anchor: str = ANCHOR_PATH
    private_key: str = PRIVATE_KEY_PATH
    receipt_dir: str = RECEIPT_DIR
    openssl: str = OPENSSL_PATH
    git: str = GIT_PATH


PRODUCTION_BOUNDARY = HostBoundary()


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    """Match the skills-contract canonical payload: sorted keys, signature omitted."""
    body = {key: payload[key] for key in sorted(payload) if key != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def provision_contract() -> dict[str, Any]:
    """Return the host-admin contract. This does not create files or keys."""
    return {
        "helper": HELPER_PATH,
        "helper_source": HELPER_SOURCE,
        "receipt_kind": RECEIPT_KIND,
        "anchor": ANCHOR_PATH,
        "private_key": PRIVATE_KEY_PATH,
        "private_key_mode": "0600",
        "receipt_dir": RECEIPT_DIR,
        "openssl": OPENSSL_PATH,
        "git": GIT_PATH,
        "terminal_paths": list(TERMINAL_PATHS),
        "worker_signing": False,
        "install": (
            "As root, copy the committed blob "
            "HEAD:tools/benchmark_receipt_verify.py to the helper path with "
            "git cat-file, then chown root:root and chmod 0755. Do not execute "
            "a dirty worktree copy. Create the receipt directory root-owned "
            "mode 0755. Create the Ed25519 key with /usr/bin/openssl as root, "
            "mode 0600, and publish only the public key to the anchor "
            "path mode 0644. Sign only by executing the installed helper. "
            "decide refuses unless the installed helper bytes equal "
            "HEAD:tools/benchmark_receipt_verify.py."
        ),
    }


def path_provenance(path: Path, *, expect_file: bool) -> bool:
    """Root-owned, non-symlink, not group/world-writable path and parent."""
    try:
        if path.is_symlink():
            return False
        if expect_file:
            if not path.is_file():
                return False
        elif not path.is_dir():
            return False
        stat = path.stat()
        if stat.st_uid != 0 or stat.st_mode & 0o022:
            return False
        parent = path.parent
        if parent.is_symlink() or not parent.is_dir():
            return False
        parent_stat = parent.stat()
        if parent_stat.st_uid != 0 or parent_stat.st_mode & 0o022:
            return False
    except OSError:
        return False
    return True


def _running_as_installed_helper() -> bool:
    try:
        invoked = Path(sys.argv[0])
        if not invoked.is_absolute() or str(invoked) != HELPER_PATH:
            return False
        if not path_provenance(invoked, expect_file=True):
            return False
        return os.access(invoked, os.X_OK)
    except OSError:
        return False


def _git_env() -> dict[str, str]:
    return {
        "PATH": "/usr/bin",
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git_blob(boundary: HostBoundary, repository: Path, spec: str) -> bytes | None:
    """Return raw committed blob bytes. Text mode is not used."""
    completed = subprocess.run(
        [
            boundary.git,
            "-C",
            str(repository),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.autocrlf=false",
            "cat-file",
            "blob",
            spec,
        ],
        check=False,
        capture_output=True,
        env=_git_env(),
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def _git(boundary: HostBoundary, repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = [
        boundary.git,
        "-C",
        str(repository),
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.autocrlf=false",
        *args,
    ]
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=_git_env(),
    )


def _ed25519_verify(boundary: HostBoundary, public_key: Path, message: bytes, signature_b64: str) -> bool:
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except Exception:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        message_path = Path(tmp) / "msg"
        signature_path = Path(tmp) / "sig"
        message_path.write_bytes(message)
        signature_path.write_bytes(signature)
        completed = subprocess.run(
            [
                boundary.openssl,
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(public_key),
                "-rawin",
                "-in",
                str(message_path),
                "-sigfile",
                str(signature_path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={"PATH": "/usr/bin", "LC_ALL": "C"},
        )
        return completed.returncode == 0


def _load_assertion(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > MAX_ASSERTION_BYTES:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("signature"), str):
        return None
    return payload


def _receipt_name(payload: dict[str, Any]) -> str | None:
    run_id = payload.get("run_id")
    lane = payload.get("lane")
    head = payload.get("system_head")
    if not isinstance(run_id, str) or RUN_RE.fullmatch(run_id) is None:
        return None
    if lane not in LANES:
        return None
    if not isinstance(head, str) or HEAD_RE.fullmatch(head) is None:
        return None
    if payload.get("kind") != RECEIPT_KIND or payload.get("lifecycle") != "COMPLETE":
        return None
    return f"{run_id}-{lane}.host-receipt.json"


def _tree_reason(boundary: HostBoundary, repository: Path, system_head: str) -> str:
    git_entry = repository / ".git"
    if git_entry.is_symlink() or not (git_entry.is_dir() or git_entry.is_file()):
        return "TRUST_BOUNDARY_UNAVAILABLE"
    if repository.is_symlink() or not repository.is_dir():
        return "TRUST_BOUNDARY_UNAVAILABLE"
    head = _git(boundary, repository, "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != system_head:
        return "HEAD_MISMATCH"
    for relative in TERMINAL_PATHS:
        worktree = repository / relative
        if worktree.is_symlink() or not worktree.is_file():
            return "BLOB_MISMATCH"
        committed = _git(boundary, repository, "rev-parse", f"HEAD:{relative}")
        observed = _git(boundary, repository, "hash-object", "--no-filters", "--", relative)
        if committed.returncode != 0 or observed.returncode != 0:
            return "BLOB_MISMATCH"
        if committed.stdout.strip() != observed.stdout.strip():
            return "BLOB_MISMATCH"
    status = _git(
        boundary,
        repository,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if status.returncode != 0 or status.stdout.strip():
        return "TREE_DIRTY"
    submodules = _git(boundary, repository, "submodule", "status", "--recursive")
    if submodules.returncode != 0:
        return "TREE_DIRTY"
    for line in submodules.stdout.splitlines():
        if line and not line.startswith(" "):
            return "TREE_DIRTY"
    return ""


def _installed_helper_reason(
    boundary: HostBoundary,
    repository: Path,
    provenance: Provenance,
) -> str:
    """Refuse when the installed helper is not the committed helper blob."""
    helper_path = Path(boundary.helper)
    if not helper_path.is_absolute() or ".." in helper_path.parts:
        return "CALLER_TRUST_PATH"
    if not provenance(helper_path, expect_file=True):
        return "TRUST_BOUNDARY_UNAVAILABLE"
    try:
        installed = helper_path.read_bytes()
    except OSError:
        return "TRUST_BOUNDARY_UNAVAILABLE"
    committed = _git_blob(boundary, repository, f"HEAD:{HELPER_SOURCE}")
    if committed is None or installed != committed:
        return "HELPER_MISMATCH"
    return ""


def _decide_receipt(
    repository: Path,
    assertion: Path,
    anchor: Path,
    *,
    boundary: HostBoundary = PRODUCTION_BOUNDARY,
    provenance: Provenance = path_provenance,
) -> str:
    """Return an empty string when the receipt qualifies, otherwise a reason.

    Terminal authority is the installed helper's ``main``. Callers of this
    function do not become that authority.
    """
    if ".." in anchor.parts or ".." in assertion.parts:
        return "CALLER_TRUST_PATH"
    if not anchor.is_absolute() or str(anchor) != boundary.anchor:
        return "CALLER_TRUST_PATH"
    if not assertion.is_absolute() or str(assertion.parent) != boundary.receipt_dir:
        return "CALLER_TRUST_PATH"
    if not provenance(Path(boundary.openssl), expect_file=True):
        return "TRUST_BOUNDARY_UNAVAILABLE"
    if not provenance(Path(boundary.git), expect_file=True):
        return "TRUST_BOUNDARY_UNAVAILABLE"
    if not provenance(Path(boundary.anchor), expect_file=True):
        return "TRUST_BOUNDARY_UNAVAILABLE"
    if not provenance(Path(boundary.receipt_dir), expect_file=False):
        return "TRUST_BOUNDARY_UNAVAILABLE"
    if not provenance(assertion, expect_file=True):
        return "TRUST_BOUNDARY_UNAVAILABLE"
    payload = _load_assertion(assertion)
    if payload is None:
        return "ASSERTION_INVALID"
    name = _receipt_name(payload)
    if name is None or assertion.name != name:
        return "ASSERTION_INVALID"
    if not _ed25519_verify(boundary, Path(boundary.anchor), canonical_payload_bytes(payload), payload["signature"]):
        return "SIGNATURE_MISMATCH"
    tree = _tree_reason(boundary, repository, str(payload["system_head"]))
    if tree:
        return tree
    return _installed_helper_reason(boundary, repository, provenance)


def _sign_unsigned(unsigned_path: Path) -> int:
    if os.geteuid() != 0 or not _running_as_installed_helper():
        sys.stderr.write("SIGNING_UNAVAILABLE\n")
        return 1
    key = Path(PRIVATE_KEY_PATH)
    if not path_provenance(key, expect_file=True):
        sys.stderr.write("SIGNING_UNAVAILABLE\n")
        return 1
    try:
        mode = key.stat().st_mode & 0o777
    except OSError:
        sys.stderr.write("SIGNING_UNAVAILABLE\n")
        return 1
    if mode != 0o600:
        sys.stderr.write("SIGNING_UNAVAILABLE\n")
        return 1
    if unsigned_path.is_symlink():
        sys.stderr.write("ASSERTION_INVALID\n")
        return 1
    try:
        payload = json.loads(unsigned_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        sys.stderr.write("ASSERTION_INVALID\n")
        return 1
    if not isinstance(payload, dict) or "signature" in payload:
        sys.stderr.write("ASSERTION_INVALID\n")
        return 1
    if payload.get("kind") != RECEIPT_KIND or payload.get("lifecycle") != "COMPLETE":
        sys.stderr.write("ASSERTION_INVALID\n")
        return 1
    run_id = payload.get("run_id")
    lane = payload.get("lane")
    head = payload.get("system_head")
    if not isinstance(run_id, str) or RUN_RE.fullmatch(run_id) is None or lane not in LANES:
        sys.stderr.write("ASSERTION_INVALID\n")
        return 1
    if not isinstance(head, str) or HEAD_RE.fullmatch(head) is None:
        sys.stderr.write("ASSERTION_INVALID\n")
        return 1
    name = f"{run_id}-{lane}.host-receipt.json"
    destination = Path(RECEIPT_DIR) / name
    if not path_provenance(Path(RECEIPT_DIR), expect_file=False):
        sys.stderr.write("TRUST_BOUNDARY_UNAVAILABLE\n")
        return 1
    message = canonical_payload_bytes(payload)
    with tempfile.TemporaryDirectory() as tmp:
        message_path = Path(tmp) / "msg"
        signature_path = Path(tmp) / "sig"
        message_path.write_bytes(message)
        completed = subprocess.run(
            [
                OPENSSL_PATH,
                "pkeyutl",
                "-sign",
                "-inkey",
                PRIVATE_KEY_PATH,
                "-rawin",
                "-in",
                str(message_path),
                "-out",
                str(signature_path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={"PATH": "/usr/bin", "LC_ALL": "C"},
        )
        if completed.returncode != 0 or not signature_path.is_file():
            sys.stderr.write("SIGNING_UNAVAILABLE\n")
            return 1
        signature = base64.b64encode(signature_path.read_bytes()).decode("ascii")
    signed = dict(payload)
    signed["signature"] = signature
    encoded = json.dumps(signed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except OSError:
        sys.stderr.write("TRUST_BOUNDARY_UNAVAILABLE\n")
        return 1
    try:
        os.write(fd, encoded)
        os.fchown(fd, 0, 0)
    finally:
        os.close(fd)
    os.chmod(temporary, 0o644)
    os.replace(temporary, destination)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if args[1:] == ["contract"]:
        json.dump(provision_contract(), sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    if len(args) == 3 and args[1] == "sign":
        return _sign_unsigned(Path(args[2]))
    if len(args) == 5 and args[1] == "decide":
        if not _running_as_installed_helper():
            sys.stderr.write("TRUST_BOUNDARY_UNAVAILABLE\n")
            return 1
        reason = _decide_receipt(Path(args[2]), Path(args[3]), Path(args[4]))
        if reason:
            sys.stderr.write(reason + "\n")
            return 1
        return 0
    sys.stderr.write("USAGE\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
