#!/usr/bin/env python3
"""Provider-neutral local binding evidence for direct implementation.

This helper is deliberately NOT a Work Packet or GitHub authority boundary.
An authenticated coordinator/connector outside the coding-agent host must first
verify the canonical Work Packet, its author permission, and the packet fields.
This process then binds those coordinator-supplied expected facts to local Git
state using only a fixed host-administered Git executable.

A PASS here is local evidence only. It never mints mutation authority by itself.
"""
from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKSTREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
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
    print(f"IMPLEMENTATION_LOCAL_BINDING=BLOCK REASON={reason}")
    print("MUTATION_AUTHORITY=NO")
    raise SystemExit(2)


def _root_administered_component(path: Path, *, expect_file: bool) -> bool:
    """Require a root-owned, non-group/world-writable path and every parent."""
    try:
        resolved = path.resolve(strict=True)
        st = resolved.lstat()
        if expect_file:
            if not stat.S_ISREG(st.st_mode) or not os.access(resolved, os.X_OK):
                return False
        elif not stat.S_ISDIR(st.st_mode):
            return False
        current = resolved
        while True:
            item = current.lstat()
            if item.st_uid != 0 or item.st_mode & 0o022:
                return False
            if current == Path("/"):
                break
            current = current.parent
    except OSError:
        return False
    return True


def resolve_trusted_git() -> Path | None:
    if _TEST_TRUSTED_GIT is not None:
        candidate = Path(_TEST_TRUSTED_GIT).resolve()
        return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None
    for candidate in TRUSTED_GIT_CANDIDATES:
        if _root_administered_component(candidate, expect_file=True):
            return candidate.resolve()
    return None


def _bounded_git_env() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }


def git(root: Path, *args: str) -> str:
    binary = resolve_trusted_git()
    if binary is None:
        raise PreflightError("LOCAL_GIT_BOUNDARY_UNAVAILABLE")
    result = subprocess.run(
        [str(binary), "-C", str(root), *args],
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
    if not host or REPO_RE.fullmatch(path) is None:
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


def check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    expected_worktree = Path(args.expected_worktree).resolve()
    if root != expected_worktree:
        raise PreflightError("WORKTREE_BINDING_MISMATCH")
    if not REPO_RE.fullmatch(args.expected_repo):
        raise PreflightError("EXPECTED_REPO_INVALID")
    if WORKSTREAM_RE.fullmatch(args.expected_workstream) is None:
        raise PreflightError("WORKSTREAM_INVALID")
    if BRANCH_RE.fullmatch(args.expected_branch) is None:
        raise PreflightError("EXPECTED_BRANCH_INVALID")
    if HEAD_RE.fullmatch(args.expected_head) is None:
        raise PreflightError("EXPECTED_HEAD_INVALID")
    if args.issue_number < 1:
        raise PreflightError("WORK_PACKET_ISSUE_INVALID")
    if args.expected_intent_revision < 1:
        raise PreflightError("INTENT_REVISION_INVALID")

    branch, head, origin = require_clean_root(root)
    origin_host, origin_repo = normalize_origin(origin)

    if origin_host != args.expected_origin_host.lower():
        raise PreflightError("ORIGIN_HOST_MISMATCH")
    if origin_repo != args.expected_repo:
        raise PreflightError("TARGET_REPO_MISMATCH")
    if branch != args.expected_branch:
        raise PreflightError("BRANCH_MISMATCH")
    if head != args.expected_head:
        raise PreflightError("HEAD_MISMATCH")

    print("IMPLEMENTATION_LOCAL_BINDING=PASS")
    print("MUTATION_AUTHORITY=NO")
    print("AUTHORITY_BOUNDARY=EXTERNAL_COORDINATOR_REQUIRED")
    print(f"TARGET_REPO={origin_repo}")
    print(f"WORKTREE={root}")
    print(f"WORKSTREAM={args.expected_workstream}")
    print(f"BRANCH={branch}")
    print(f"HEAD={head}")
    print(f"INTENT_REVISION={args.expected_intent_revision}")
    print(f"CHANGE_RISK={args.expected_change_risk}")
    print(f"IMPLEMENTER={DIRECT_CHAT_IMPLEMENTER}")
    print(f"PACKET_ISSUE={args.issue_number}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("check")
    command.add_argument("--root", required=True)
    command.add_argument("--expected-worktree", required=True)
    command.add_argument("--issue-number", required=True, type=int)
    command.add_argument("--expected-repo", required=True)
    command.add_argument("--expected-origin-host", default="github.com")
    command.add_argument("--expected-workstream", required=True)
    command.add_argument("--expected-branch", required=True)
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
    except PreflightError as exc:
        fail(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
