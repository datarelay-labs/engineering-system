#!/usr/bin/env python3
"""Root-administered signer for one exact Engineering System external-write effect.

This helper mints only the binding/dispatch assertions already consumed by
skills-contract.py and worker_adapter.py. It performs no GitHub/network write,
accepts no command/URL/private-key override, and signs only a strictly parsed
worker-adapter effect whose repository/worktree identity is verified locally.

Production installation is expected at a root-owned fixed path with a
root-readable Ed25519 private key. The coding worker must not be able to read
or replace either file.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

OPENSSL = Path("/usr/bin/openssl")
HOST_PRIVATE_KEY = Path("/etc/engineering-system/skills-trust-anchor.key")
HOST_PUBLIC_KEY = Path("/etc/engineering-system/skills-trust-anchor.pub")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKSTREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ALLOWED_ACTIONS = frozenset(
    {"update_work_packet", "comment_work_packet", "create_pull_request", "update_pull_request", "authorize_publication"}
)
ALLOWED_STATUSES = frozenset({"ACTIVE", "PAUSED", "BLOCKED", "COMPLETE"})
ALLOWED_TARGET_KINDS = frozenset({"issue", "pull_request"})
EFFECT_KEYS = frozenset(
    {"branch", "expected_status", "intent_revision", "mutation_content", "mutation_target",
     "requested_action", "subject_head", "target_repo", "workstream"}
)
TARGET_KEYS = frozenset({"kind", "id"})
MAX_CONTENT_BYTES = 256 * 1024
MAX_TTL_SECONDS = 300
_TEST_MODE = False


class SignerError(Exception):
    pass


def _load_skills(root: Path):
    path = Path("/usr/lib/engineering-system/skills-contract.py") if not _TEST_MODE else root / "tools" / "skills-contract.py"
    spec = importlib.util.spec_from_file_location("trusted_signer_skills_contract", path)
    if spec is None or spec.loader is None:
        raise SignerError("skills contract helper unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _root_owned_private(path: Path) -> bool:
    try:
        st = path.stat()
        parent = path.parent.stat()
    except OSError:
        return False
    return (
        path.is_file()
        and st.st_uid == 0
        and (st.st_mode & 0o077) == 0
        and parent.st_uid == 0
        and (parent.st_mode & 0o022) == 0
    )


def _root_owned_public(path: Path) -> bool:
    try:
        st = path.stat()
        parent = path.parent.stat()
    except OSError:
        return False
    return (
        path.is_file()
        and st.st_uid == 0
        and (st.st_mode & 0o022) == 0
        and parent.st_uid == 0
        and (parent.st_mode & 0o022) == 0
    )


def _openssl_ok() -> bool:
    try:
        st = OPENSSL.stat()
        parent = OPENSSL.parent.stat()
    except OSError:
        return False
    return (
        OPENSSL.is_file()
        and st.st_uid == 0 and (st.st_mode & 0o022) == 0
        and parent.st_uid == 0 and (parent.st_mode & 0o022) == 0
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SignerError(f"invalid effect JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise SignerError("effect must be a JSON object")
    return raw


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SignerError(f"{label} must be a non-empty string")
    return value.strip()


def _parse_effect(raw: dict[str, Any]) -> dict[str, Any]:
    if set(raw) != EFFECT_KEYS:
        raise SignerError("effect keys do not match the trusted external-write schema")
    repo = _nonempty(raw["target_repo"], "target_repo")
    if REPOSITORY_RE.fullmatch(repo) is None:
        raise SignerError("target_repo must be owner/name")
    workstream = _nonempty(raw["workstream"], "workstream")
    if WORKSTREAM_RE.fullmatch(workstream) is None:
        raise SignerError("invalid workstream")
    branch = _nonempty(raw["branch"], "branch")
    if len(branch) > 240 or any(ch in branch for ch in "\r\n\x00"):
        raise SignerError("invalid branch")
    head = _nonempty(raw["subject_head"], "subject_head").lower()
    if FULL_SHA_RE.fullmatch(head) is None:
        raise SignerError("subject_head must be a lowercase full SHA")
    revision = raw["intent_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise SignerError("intent_revision must be >= 1")
    action = _nonempty(raw["requested_action"], "requested_action")
    if action not in ALLOWED_ACTIONS:
        raise SignerError("requested_action is not allowed")
    status = _nonempty(raw["expected_status"], "expected_status").upper()
    if status not in ALLOWED_STATUSES:
        raise SignerError("expected_status is not allowed")
    content = raw["mutation_content"]
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise SignerError("mutation_content is invalid or too large")
    target = raw["mutation_target"]
    if not isinstance(target, dict) or set(target) != TARGET_KEYS:
        raise SignerError("mutation_target must contain only kind and id")
    kind = _nonempty(target["kind"], "mutation_target.kind")
    if kind not in ALLOWED_TARGET_KINDS:
        raise SignerError("mutation_target.kind is not allowed")
    target_id = _nonempty(target["id"], "mutation_target.id")
    if any(ch in target_id for ch in "\r\n\x00"):
        raise SignerError("mutation_target.id is invalid")
    return {
        "branch": branch, "expected_status": status, "intent_revision": revision,
        "mutation_content": content, "mutation_target": {"kind": kind, "id": target_id},
        "requested_action": action, "subject_head": head, "target_repo": repo, "workstream": workstream,
    }


def _git(root: Path, *args: str) -> str:
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/nonexistent", "XDG_CONFIG_HOME": "/nonexistent"}
    cp = subprocess.run(
        ["/usr/bin/git", "-c", f"safe.directory={root}", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
        cwd=root, env=env, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if cp.returncode != 0:
        raise SignerError("trusted git identity check failed")
    return cp.stdout.strip()


def _verify_local_scope(root: Path, effect: dict[str, Any]) -> None:
    if not root.is_dir() or root.is_symlink():
        raise SignerError("worktree must be a real directory")
    top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top != root.resolve():
        raise SignerError("worktree root mismatch")
    current_branch = _git(root, "branch", "--show-current")
    if current_branch and current_branch != effect["branch"]:
        raise SignerError("branch mismatch")
    if not current_branch and not _TEST_MODE:
        raise SignerError("detached HEAD is not allowed for production signing")
    if _git(root, "rev-parse", "HEAD") != effect["subject_head"]:
        raise SignerError("subject_head mismatch")
    origin = _git(root, "remote", "get-url", "origin")
    suffix = effect["target_repo"]
    accepted = {
        f"https://github.com/{suffix}", f"https://github.com/{suffix}.git",
        f"git@github.com:{suffix}", f"git@github.com:{suffix}.git",
        f"ssh://git@github.com/{suffix}", f"ssh://git@github.com/{suffix}.git",
    }
    if origin not in accepted:
        raise SignerError("origin repository mismatch")


def _canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    body = {key: payload[key] for key in sorted(payload) if key != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign(private_key: Path, payload: dict[str, Any]) -> dict[str, Any]:
    message = _canonical_payload_bytes(payload)
    with tempfile.TemporaryDirectory() as tmp:
        msg = Path(tmp) / "msg"
        sig = Path(tmp) / "sig"
        msg.write_bytes(message)
        cp = subprocess.run(
            [str(OPENSSL), "pkeyutl", "-sign", "-inkey", str(private_key), "-rawin", "-in", str(msg), "-out", str(sig)],
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if cp.returncode != 0:
            raise SignerError("host signing failed")
        out = dict(payload)
        out["signature"] = base64.b64encode(sig.read_bytes()).decode("ascii")
        return out


def _atomic_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def issue_assertions(args: argparse.Namespace) -> int:
    if not sys.platform.startswith("linux") or not _openssl_ok():
        raise SignerError("fixed OpenSSL trust boundary unavailable")
    root = args.root.resolve()
    private_key = args.private_key
    public_key = args.public_key
    if not args.test_mode:
        private_key = HOST_PRIVATE_KEY
        public_key = HOST_PUBLIC_KEY
        if os.geteuid() != 0:
            raise SignerError("production signer must run as root")
        if not _root_owned_private(private_key) or not _root_owned_public(public_key):
            raise SignerError("production key provenance invalid")
    else:
        if private_key is None or public_key is None:
            raise SignerError("test mode requires explicit keypair")
    effect = _parse_effect(_read_json(args.effect_json))
    _verify_local_scope(root, effect)
    if args.authority_permission not in {"write", "maintain", "admin"}:
        raise SignerError("authenticated authority_permission is required")
    skills = _load_skills(root)
    _, _, digest = skills.load_effective_state(root)
    public_hash = hashlib.sha256(public_key.read_bytes()).hexdigest()
    session_id = args.session_id
    if SESSION_ID_RE.fullmatch(session_id) is None:
        raise SignerError("invalid session_id")
    scope = {
        "target_repo": effect["target_repo"], "worktree": str(root),
        "workstream": effect["workstream"], "branch": effect["branch"],
        "subject_head": effect["subject_head"], "intent_revision": effect["intent_revision"],
        "session_id": session_id,
    }
    binding_unsigned = {
        "profile": "external_write", "policy_digest": digest, "authority_permission": args.authority_permission,
        "approved_classes": ["external_write"], "public_key_sha256": public_hash, "scope": scope,
    }
    binding = _sign(private_key, binding_unsigned)
    now = int(time.time())
    ttl = args.ttl_seconds
    if ttl < 1 or ttl > MAX_TTL_SECONDS:
        raise SignerError(f"ttl_seconds must be 1..{MAX_TTL_SECONDS}")
    dispatch_unsigned = {
        "tool_id": "network.post", "classes": ["external_write", "network"],
        "policy_digest": digest, "binding_public_key_sha256": public_hash,
        "binding_sha256": skills.signed_payload_sha256(binding), "worktree": str(root),
        "request_sha256": skills.canonical_request_sha256(effect),
        "session_id": session_id, "scope_sha256": skills.canonical_request_sha256(scope),
        "dispatch_id": args.dispatch_id, "expires_at_unix": now + ttl,
    }
    if SESSION_ID_RE.fullmatch(args.dispatch_id) is None:
        raise SignerError("invalid dispatch_id")
    dispatch = _sign(private_key, dispatch_unsigned)
    _atomic_private_json(args.binding_out, binding)
    _atomic_private_json(args.dispatch_out, dispatch)
    print("TRUSTED_EXTERNAL_WRITE_SIGNER=PASS")
    print(f"REQUEST_SHA256={skills.canonical_request_sha256(effect)}")
    print(f"EXPIRES_AT_UNIX={now + ttl}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue")
    issue.add_argument("--root", type=Path, required=True)
    issue.add_argument("--effect-json", type=Path, required=True)
    issue.add_argument("--binding-out", type=Path, required=True)
    issue.add_argument("--dispatch-out", type=Path, required=True)
    issue.add_argument("--session-id", required=True)
    issue.add_argument("--dispatch-id", required=True)
    issue.add_argument("--authority-permission", choices=("write", "maintain", "admin"), help=argparse.SUPPRESS)
    issue.add_argument("--ttl-seconds", type=int, default=120)
    issue.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    issue.add_argument("--private-key", type=Path, help=argparse.SUPPRESS)
    issue.add_argument("--public-key", type=Path, help=argparse.SUPPRESS)
    issue.set_defaults(func=issue_assertions)
    return p


def main() -> int:
    global _TEST_MODE
    args = build_parser().parse_args()
    _TEST_MODE = bool(args.test_mode)
    try:
        return args.func(args)
    except SignerError as exc:
        print(f"TRUSTED_EXTERNAL_WRITE_SIGNER=BLOCK reason={exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
