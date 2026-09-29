#!/usr/bin/env python3
"""Fail-closed provider-neutral pre-mutation binding for an implementation worker.

This CLI performs the Work Packet and author-permission reads itself through
the host's authenticated GitHub CLI. Caller-supplied packet bodies, digests,
permissions, or token environment variables do not mint repository authority.
The authenticated GitHub facts are then bound to exact local Git state.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
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
TRUSTED_ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
TRUSTED_GH_CANDIDATES = (
    Path("/usr/bin/gh"),
    Path("/usr/local/bin/gh"),
    TRUSTED_ACCOUNT_HOME / ".local/bin/gh",
    Path("/opt/homebrew/bin/gh"),
)
TRUSTED_GIT_CANDIDATES = (
    Path("/usr/bin/git"),
    Path("/usr/local/bin/git"),
    Path("/opt/homebrew/bin/git"),
)
_TEST_TRUSTED_GH: Path | None = None
_TEST_TRUSTED_GIT: Path | None = None


class PreflightError(ValueError):
    pass


def fail(reason: str) -> None:
    print(f"IMPLEMENTATION_PREFLIGHT=BLOCK REASON={reason}")
    raise SystemExit(2)


def git(root: Path, *args: str) -> str:
    binary = resolve_trusted_git(root)
    if binary is None:
        raise PreflightError("GIT_STATE_UNAVAILABLE")
    result = subprocess.run(
        [str(binary), "-C", str(root), *args],
        cwd="/",
        env=_bounded_local_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        raise PreflightError("GIT_STATE_UNAVAILABLE")
    return result.stdout.strip()


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
        parent = path.parent.lstat()
        if stat.S_ISLNK(parent.st_mode) or parent.st_mode & 0o022:
            return False
        if root == path or root in path.parents:
            return False
        if not test_override:
            fixed = [candidate.resolve() for candidate in candidates if candidate.exists()]
            if path not in fixed:
                return False
    except OSError:
        return False
    return True


def resolve_trusted_executable(
    root: Path, candidates: tuple[Path, ...], override: Path | None
) -> Path | None:
    if override is not None:
        candidate = Path(override)
        return candidate.resolve() if _executable_provenance_ok(
            candidate, root, candidates, test_override=True
        ) else None
    for candidate in candidates:
        if candidate.exists() and _executable_provenance_ok(candidate, root, candidates):
            return candidate.resolve()
    return None


def resolve_trusted_gh(root: Path) -> Path | None:
    return resolve_trusted_executable(root, TRUSTED_GH_CANDIDATES, _TEST_TRUSTED_GH)


def resolve_trusted_git(root: Path) -> Path | None:
    return resolve_trusted_executable(root, TRUSTED_GIT_CANDIDATES, _TEST_TRUSTED_GIT)


def _bounded_local_env() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "HOME": str(TRUSTED_ACCOUNT_HOME),
    }


def _bounded_gh_env() -> dict[str, str]:
    env = _bounded_local_env()
    env["GH_PROMPT_DISABLED"] = "1"
    return env


def github_json(root: Path, hostname: str, endpoint: str) -> dict:
    gh_path = resolve_trusted_gh(root)
    if gh_path is None:
        raise PreflightError("WORK_PACKET_PROVENANCE_UNTRUSTED")

    result = subprocess.run(
        [str(gh_path), "api", "--hostname", hostname, endpoint],
        cwd="/",
        env=_bounded_gh_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        raise PreflightError("WORK_PACKET_PROVENANCE_UNTRUSTED")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PreflightError("WORK_PACKET_PROVENANCE_UNTRUSTED") from exc
    if not isinstance(payload, dict):
        raise PreflightError("WORK_PACKET_PROVENANCE_UNTRUSTED")
    return payload


def authenticated_packet(
    root: Path, hostname: str, repo: str, issue_number: int
) -> tuple[str, str, str]:
    if issue_number < 1:
        raise PreflightError("WORK_PACKET_ISSUE_INVALID")
    issue = github_json(root, hostname, f"repos/{repo}/issues/{issue_number}")
    if issue.get("pull_request") is not None:
        raise PreflightError("WORK_PACKET_ISSUE_INVALID")
    if str(issue.get("state") or "").lower() != "open":
        raise PreflightError("WORK_PACKET_NOT_ACTIVE")
    if not str(issue.get("title") or "").startswith("[AI Work]"):
        raise PreflightError("WORK_PACKET_TITLE_INVALID")

    body = issue.get("body")
    user = issue.get("user")
    author = user.get("login") if isinstance(user, dict) else None
    if not isinstance(body, str) or not body.strip():
        raise PreflightError("WORK_PACKET_UNREADABLE")
    if not isinstance(author, str) or re.fullmatch(r"[A-Za-z0-9-]{1,39}", author) is None:
        raise PreflightError("WORK_PACKET_AUTHOR_UNTRUSTED")

    permission_payload = github_json(
        root, hostname, f"repos/{repo}/collaborators/{author}/permission"
    )
    try:
        permission = work_packet_authority.authorize_work_packet_author_permission(
            permission_payload.get("permission")
        )
    except SystemExit as exc:
        raise PreflightError("WORK_PACKET_AUTHOR_UNTRUSTED") from exc
    return body, author, permission


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

    body, author, permission = authenticated_packet(
        root, origin_host, origin_repo, args.issue_number
    )
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
    print(f"TARGET_REPO={target_repo}")
    print(f"WORKTREE={root}")
    print(f"WORKSTREAM={workstream}")
    print(f"BRANCH={branch}")
    print(f"HEAD={head}")
    print(f"INTENT_REVISION={packet_revision}")
    print(f"CHANGE_RISK={change_risk}")
    print(f"IMPLEMENTER={implementer}")
    print(f"PACKET_ISSUE={args.issue_number}")
    print(f"PACKET_AUTHOR={author}")
    print(f"AUTHOR_PERMISSION={permission}")
    print(f"PACKET_BODY_SHA256={packet.body_sha256}")
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
