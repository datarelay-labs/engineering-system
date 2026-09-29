#!/usr/bin/env python3
"""Fail-closed local Git binding evidence for Chat-primary implementation.

GitHub Work Packet and author-permission authority belongs to the external,
authenticated coordinator/connector. This helper performs no network or GitHub
read and cannot mint mutation authority. It only proves that coordinator-supplied
expected repository facts match one clean local worktree through a
host-administered, config-isolated Git executable.
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
CHANGE_RISKS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
DIRECT_CHAT_IMPLEMENTER = "CHATGPT_CHAT"
AUTHORITY_BOUNDARY = "EXTERNAL_AUTHENTICATED_GITHUB_COORDINATOR_REQUIRED"
TRUSTED_GIT_CANDIDATES = (
    Path("/usr/bin/git"),
    Path("/usr/local/bin/git"),
    Path("/opt/homebrew/bin/git"),
)


class PreflightError(ValueError):
    pass


def fail(reason: str) -> None:
    print(f"IMPLEMENTATION_LOCAL_BINDING=BLOCK REASON={reason}")
    print("MUTATION_AUTHORITY=NO")
    raise SystemExit(2)


def _lexical_components(path: Path) -> list[Path]:
    if not path.is_absolute():
        return []
    parts = path.parts
    current = Path(parts[0])
    out = [current]
    for part in parts[1:]:
        current = current / part
        out.append(current)
    return out


def _root_administered_path(path: Path, *, executable: bool) -> bool:
    """Require root-owned, worker-inaccessible lexical and resolved path components."""
    if os.geteuid() == 0:
        return False
    try:
        lexical = path.absolute()
        for component in _lexical_components(lexical):
            st = component.lstat()
            if stat.S_ISLNK(st.st_mode):
                return False
            if st.st_uid != 0 or st.st_mode & 0o022:
                return False
            if os.geteuid() != 0 and os.access(component, os.W_OK):
                return False

        resolved = lexical.resolve(strict=True)
        for component in _lexical_components(resolved):
            st = component.lstat()
            if stat.S_ISLNK(st.st_mode):
                return False
            if st.st_uid != 0 or st.st_mode & 0o022:
                return False
            if os.geteuid() != 0 and os.access(component, os.W_OK):
                return False

        final = resolved.lstat()
        if not stat.S_ISREG(final.st_mode):
            return False
        if executable and not os.access(resolved, os.X_OK):
            return False
    except OSError:
        return False
    return True


def resolve_trusted_git() -> Path | None:
    for candidate in TRUSTED_GIT_CANDIDATES:
        if _root_administered_path(candidate, executable=True):
            return candidate.resolve()
    return None


def _bounded_git_env() -> dict[str, str]:
    """Environment for local identity reads; inherit no caller Git configuration."""
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "HOME": "/nonexistent",
        "XDG_CONFIG_HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }


def git(root: Path, *args: str) -> str:
    binary = resolve_trusted_git()
    if binary is None:
        raise PreflightError("LOCAL_GIT_BOUNDARY_UNAVAILABLE")
    command = [
        str(binary),
        "-C",
        str(root),
        "--no-replace-objects",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "credential.helper=",
        "-c",
        "submodule.recurse=false",
        *args,
    ]
    result = subprocess.run(
        command,
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


def _index_has_hidden_flags(root: Path) -> bool:
    records = git(root, "ls-files", "-v", "-z").split("\0")
    for record in records:
        if not record:
            continue
        tag = record[0]
        if tag == "S" or tag.islower():
            return True
    return False


def _initialized_gitlink_roots(root: Path) -> list[Path]:
    raw = git(root, "ls-files", "--stage", "-z")
    submodules: list[Path] = []
    for record in raw.split("\0"):
        if not record:
            continue
        metadata, separator, relative = record.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise PreflightError("GIT_STATE_UNAVAILABLE")
        if fields[0] != "160000":
            continue
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PreflightError("SUBMODULE_STATE_UNAVAILABLE") from exc
        if not candidate.is_dir():
            continue
        top = Path(git(candidate, "rev-parse", "--show-toplevel")).resolve()
        if top == candidate:
            submodules.append(candidate)
    return submodules


def _reject_hidden_index_flags(root: Path, seen: set[Path] | None = None) -> None:
    visited = seen if seen is not None else set()
    resolved = root.resolve()
    if resolved in visited:
        raise PreflightError("SUBMODULE_STATE_UNAVAILABLE")
    visited.add(resolved)
    if _index_has_hidden_flags(resolved):
        raise PreflightError("HIDDEN_INDEX_STATE")
    for submodule in _initialized_gitlink_roots(resolved):
        _reject_hidden_index_flags(submodule, visited)


def require_clean_root(root: Path) -> tuple[str, str, str]:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise PreflightError("WORKTREE_UNAVAILABLE")
    top = Path(git(resolved, "rev-parse", "--show-toplevel")).resolve()
    if top != resolved:
        raise PreflightError("WORKTREE_ROOT_MISMATCH")
    branch = git(resolved, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch:
        raise PreflightError("DETACHED_HEAD")
    head = git(resolved, "rev-parse", "--verify", "HEAD^{commit}")
    if HEAD_RE.fullmatch(head) is None:
        raise PreflightError("HEAD_INVALID")
    _reject_hidden_index_flags(resolved)
    if git(
        resolved,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
    ):
        raise PreflightError("WORKTREE_DIRTY")
    origin = git(
        resolved,
        "config",
        "--local",
        "--no-includes",
        "--get",
        "remote.origin.url",
    )
    return branch, head, origin


def _safe_branch(value: str) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 255
        and value == value.strip()
        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
    )


def check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    expected_worktree = Path(args.expected_worktree).resolve()
    if root != expected_worktree:
        raise PreflightError("WORKTREE_BINDING_MISMATCH")
    if REPO_RE.fullmatch(args.expected_repo) is None:
        raise PreflightError("EXPECTED_REPO_INVALID")
    if WORKSTREAM_RE.fullmatch(args.expected_workstream) is None:
        raise PreflightError("WORKSTREAM_INVALID")
    if not _safe_branch(args.expected_branch):
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
    print("AUTHORITY_BOUNDARY=EXTERNAL_AUTHENTICATED_GITHUB_COORDINATOR_REQUIRED")
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
