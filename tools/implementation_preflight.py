#!/usr/bin/env python3
"""Fail-closed local binding evidence for a Chat/remote implementation worker.

Authenticated Work Packet body and author-permission authority belong to an
external GitHub connector/coordinator outside the remote coding-worker
privilege boundary. This CLI binds connector-supplied packet bytes and
permission attestation to exact local Git state using a fixed
host-administered Git executable and config-isolated invocation. It does not
perform GitHub reads and cannot mint mutation authority.
"""
from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys

# This helper is itself a pre-mutation gate. Importing its managed sibling
# modules must not create __pycache__ and dirty the target worktree.
sys.dont_write_bytecode = True

from pathlib import Path
from urllib.parse import urlsplit

import context_epoch
import work_packet_authority

HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
IMPLEMENTER_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
CHANGE_RISKS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
DIRECT_CHAT_IMPLEMENTER = "CHATGPT_CHAT"
TRUSTED_GIT_CANDIDATES = (
    Path("/usr/bin/git"),
    Path("/usr/local/bin/git"),
    Path("/opt/homebrew/bin/git"),
)
_TEST_TRUSTED_GIT: Path | None = None


class PreflightError(ValueError):
    pass


def fail(reason: str) -> None:
    print(f"IMPLEMENTATION_PREFLIGHT=BLOCK REASON={reason}")
    raise SystemExit(2)


def _path_components(path: Path) -> list[Path]:
    resolved = path.resolve(strict=True)
    components = [resolved]
    current = resolved.parent
    while True:
        components.append(current)
        if current == current.parent:
            break
        current = current.parent
    return components


def _independently_administered(path: Path) -> bool:
    """Require host-administered ownership the implementing account cannot rewrite."""
    uid = os.getuid()
    try:
        for component in _path_components(path):
            st = component.lstat()
            if stat.S_ISLNK(st.st_mode):
                return False
            if st.st_mode & 0o022:
                return False
            if uid != 0 and st.st_uid == uid:
                return False
            if uid != 0 and os.access(component, os.W_OK):
                return False
    except OSError:
        return False
    return True


def _executable_provenance_ok(
    path: Path,
    root: Path,
    candidates: tuple[Path, ...],
    *,
    test_override: bool = False,
) -> bool:
    try:
        path = path.resolve(strict=True)
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return False
        if not os.access(path, os.X_OK) or st.st_mode & 0o022:
            return False
        if root == path or root in path.parents:
            return False
        if not test_override:
            fixed = [candidate.resolve() for candidate in candidates if candidate.exists()]
            if path not in fixed:
                return False
            if not _independently_administered(path):
                return False
        else:
            parent = path.parent.lstat()
            if stat.S_ISLNK(parent.st_mode) or parent.st_mode & 0o022:
                return False
    except OSError:
        return False
    return True


def resolve_trusted_git(root: Path) -> Path | None:
    if _TEST_TRUSTED_GIT is not None:
        candidate = Path(_TEST_TRUSTED_GIT)
        return candidate.resolve() if _executable_provenance_ok(
            candidate, root, TRUSTED_GIT_CANDIDATES, test_override=True
        ) else None
    for candidate in TRUSTED_GIT_CANDIDATES:
        if candidate.exists() and _executable_provenance_ok(
            candidate, root, TRUSTED_GIT_CANDIDATES
        ):
            return candidate.resolve()
    return None


def _bounded_git_env() -> dict[str, str]:
    # Isolate from caller/repo Git config, hooks, and external helpers such as
    # fsmonitor. HOME is intentionally absent so no account config is loaded.
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
    }


def git(root: Path, *args: str) -> str:
    binary = resolve_trusted_git(root)
    if binary is None:
        raise PreflightError("GIT_STATE_UNAVAILABLE")
    result = subprocess.run(
        [
            str(binary),
            "-C",
            str(root),
            "--no-replace-objects",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=",
            *args,
        ],
        cwd="/",
        env=_bounded_git_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        raise PreflightError("GIT_STATE_UNAVAILABLE")
    return result.stdout.strip()


def normalize_origin(url: str) -> tuple[str, str]:
    raw = url.strip()
    if not raw or any(ch in raw for ch in "\r\n\0"):
        raise PreflightError("ORIGIN_INVALID")
    if "://" in raw:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lstrip("/")
    elif raw.startswith("git@") and ":" in raw:
        user_host, path = raw.split(":", 1)
        host = user_host.split("@", 1)[1].lower()
    else:
        raise PreflightError("ORIGIN_INVALID")
    if path.endswith(".git"):
        path = path[:-4]
    if not host or not context_epoch.SAFE_REPO_RE.fullmatch(path):
        raise PreflightError("ORIGIN_INVALID")
    return host, path


def require_clean_root(root: Path) -> tuple[str, str, str]:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise PreflightError("WORKTREE_UNAVAILABLE")
    top = Path(git(resolved, "rev-parse", "--show-toplevel")).resolve()
    if top != resolved:
        raise PreflightError("WORKTREE_ROOT_MISMATCH")
    branch = git(resolved, "branch", "--show-current")
    if not branch:
        raise PreflightError("DETACHED_HEAD")
    head = git(resolved, "rev-parse", "HEAD")
    if HEAD_RE.fullmatch(head) is None:
        raise PreflightError("HEAD_INVALID")
    if git(resolved, "status", "--porcelain", "--untracked-files=all"):
        raise PreflightError("WORKTREE_DIRTY")
    origin = git(resolved, "remote", "get-url", "origin")
    return branch, head, origin


def load_connector_packet(path: Path) -> str:
    try:
        body = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PreflightError("WORK_PACKET_UNREADABLE") from exc
    if not body.strip():
        raise PreflightError("WORK_PACKET_UNREADABLE")
    return body


def check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    expected_worktree = Path(args.expected_worktree).resolve()
    if root != expected_worktree:
        raise PreflightError("WORKTREE_BINDING_MISMATCH")
    branch, head, origin = require_clean_root(root)
    origin_host, origin_repo = normalize_origin(origin)

    if origin_host != args.expected_origin_host.lower():
        raise PreflightError("ORIGIN_HOST_MISMATCH")
    if origin_repo != args.expected_repo:
        raise PreflightError("TARGET_REPO_MISMATCH")

    try:
        permission = work_packet_authority.authorize_work_packet_author_permission(
            args.connector_attested_author_permission
        )
    except SystemExit as exc:
        raise PreflightError("WORK_PACKET_AUTHOR_UNTRUSTED") from exc

    body = load_connector_packet(Path(args.packet_body_file))
    packet = context_epoch.parse_packet(body)
    audit = context_epoch.analyze_packet(packet)
    if audit["blocking"]:
        raise PreflightError("WORK_PACKET_INVALID:" + ",".join(audit["blocking"]))
    if packet.metadata.get("STATUS") != "ACTIVE":
        raise PreflightError("WORK_PACKET_NOT_ACTIVE")

    target_repo = packet.metadata.get("TARGET_REPO", "")
    workstream = packet.metadata.get("WORKSTREAM", "")
    packet_branch = packet.metadata.get("BRANCH", "")
    packet_head = packet.metadata.get("LAST_VERIFIED_HEAD", "")
    packet_revision = packet.metadata.get("INTENT_REVISION", "")
    implementer = packet.metadata.get("IMPLEMENTER", "")
    change_risk = packet.metadata.get("CHANGE_RISK", "")

    if target_repo != origin_repo or target_repo != args.expected_repo:
        raise PreflightError("TARGET_REPO_MISMATCH")
    if workstream != args.expected_workstream:
        raise PreflightError("WORKSTREAM_MISMATCH")
    if packet_branch != branch:
        raise PreflightError("BRANCH_MISMATCH")
    if packet_head != head or head != args.expected_head:
        raise PreflightError("HEAD_MISMATCH")
    if HEAD_RE.fullmatch(packet_head) is None:
        raise PreflightError("PACKET_HEAD_INVALID")
    if packet_revision != str(args.expected_intent_revision):
        raise PreflightError("STALE_INTENT_REVISION")
    if not implementer or IMPLEMENTER_RE.fullmatch(implementer) is None:
        raise PreflightError("IMPLEMENTER_INVALID")
    if implementer != DIRECT_CHAT_IMPLEMENTER:
        raise PreflightError("IMPLEMENTER_MISMATCH")
    if change_risk not in CHANGE_RISKS:
        raise PreflightError("CHANGE_RISK_INVALID")
    if change_risk != args.expected_change_risk:
        raise PreflightError("CHANGE_RISK_MISMATCH")

    print("IMPLEMENTATION_PREFLIGHT=PASS")
    print("LOCAL_BINDING=PASS")
    print("MUTATION_AUTHORITY=NOT_GRANTED")
    print("AUTHORITY_BOUNDARY=EXTERNAL_GITHUB_CONNECTOR")
    print(f"TARGET_REPO={target_repo}")
    print(f"WORKTREE={root}")
    print(f"WORKSTREAM={workstream}")
    print(f"BRANCH={branch}")
    print(f"HEAD={head}")
    print(f"INTENT_REVISION={packet_revision}")
    print(f"CHANGE_RISK={change_risk}")
    print(f"IMPLEMENTER={implementer}")
    if args.issue_number is not None:
        print(f"PACKET_ISSUE={args.issue_number}")
    print(f"CONNECTOR_ATTESTED_AUTHOR_PERMISSION={permission}")
    print(f"PACKET_BODY_SHA256={packet.body_sha256}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("check")
    command.add_argument("--root", required=True)
    command.add_argument("--expected-worktree", required=True)
    command.add_argument(
        "--packet-body-file",
        required=True,
        help="Connector-authenticated Work Packet body bytes for local binding only",
    )
    command.add_argument(
        "--connector-attested-author-permission",
        required=True,
        help=(
            "Author permission attested by the external GitHub connector after an "
            "authenticated collaborators/{author}/permission read; format-checked only"
        ),
    )
    command.add_argument("--issue-number", required=False, type=int, default=None)
    command.add_argument("--expected-repo", required=True)
    command.add_argument("--expected-origin-host", default="github.com")
    command.add_argument("--expected-workstream", required=True)
    command.add_argument("--expected-head", required=True)
    command.add_argument("--expected-intent-revision", required=True, type=int)
    command.add_argument(
        "--expected-change-risk",
        required=True,
        choices=sorted(CHANGE_RISKS),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "check":
            return check(args)
        raise PreflightError("COMMAND_INVALID")
    except (PreflightError, context_epoch.ContextError) as exc:
        fail(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
