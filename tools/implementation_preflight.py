#!/usr/bin/env python3
"""Fail-closed provider-neutral pre-mutation binding for an implementation worker.

This CLI does not authenticate GitHub by itself. A trusted coordinator must
supply the exact Work Packet body bytes/digest and effective author permission
from fresh authenticated GitHub reads. The tool binds those facts to local Git
state; it does not mint repository authority from caller-provided labels.
"""
from __future__ import annotations

import argparse
import re
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
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMPLEMENTER_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
CHANGE_RISKS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})


class PreflightError(ValueError):
    pass


def fail(reason: str) -> None:
    print(f"IMPLEMENTATION_PREFLIGHT=BLOCK REASON={reason}")
    raise SystemExit(2)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
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


def check(args: argparse.Namespace) -> int:
    try:
        body = Path(args.packet_body_file).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PreflightError("WORK_PACKET_UNREADABLE") from exc

    packet = context_epoch.parse_packet(body)
    trusted_digest = args.expected_packet_body_sha256.strip().lower()
    if SHA256_RE.fullmatch(trusted_digest) is None:
        raise PreflightError("TRUSTED_PACKET_DIGEST_INVALID")
    if packet.body_sha256 != trusted_digest:
        raise PreflightError("WORK_PACKET_DIGEST_MISMATCH")
    audit = context_epoch.analyze_packet(packet)
    if audit["blocking"]:
        raise PreflightError("WORK_PACKET_INVALID:" + ",".join(audit["blocking"]))
    if packet.metadata.get("STATUS") != "ACTIVE":
        raise PreflightError("WORK_PACKET_NOT_ACTIVE")

    try:
        permission = work_packet_authority.authorize_work_packet_author_permission(
            args.author_permission
        )
    except SystemExit as exc:
        raise PreflightError("WORK_PACKET_AUTHOR_UNTRUSTED") from exc

    root = Path(args.root).resolve()
    expected_worktree = Path(args.expected_worktree).resolve()
    if root != expected_worktree:
        raise PreflightError("WORKTREE_BINDING_MISMATCH")
    branch, head, origin = require_clean_root(root)
    origin_host, origin_repo = normalize_origin(origin)

    target_repo = packet.metadata.get("TARGET_REPO", "")
    workstream = packet.metadata.get("WORKSTREAM", "")
    packet_branch = packet.metadata.get("BRANCH", "")
    packet_head = packet.metadata.get("LAST_VERIFIED_HEAD", "")
    packet_revision = packet.metadata.get("INTENT_REVISION", "")
    implementer = packet.metadata.get("IMPLEMENTER", "")
    change_risk = packet.metadata.get("CHANGE_RISK", "")

    if origin_host != args.expected_origin_host.lower():
        raise PreflightError("ORIGIN_HOST_MISMATCH")
    if origin_repo != target_repo or target_repo != args.expected_repo:
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
    if implementer != args.expected_implementer:
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
    print(f"AUTHOR_PERMISSION={permission}")
    print(f"PACKET_BODY_SHA256={packet.body_sha256}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("check")
    command.add_argument("--root", required=True)
    command.add_argument("--expected-worktree", required=True)
    command.add_argument("--packet-body-file", required=True)
    command.add_argument("--expected-packet-body-sha256", required=True)
    command.add_argument(
        "--author-permission",
        required=True,
        help="Fresh effective permission from an authenticated GitHub collaborator read.",
    )
    command.add_argument("--expected-repo", required=True)
    command.add_argument("--expected-origin-host", default="github.com")
    command.add_argument("--expected-workstream", required=True)
    command.add_argument("--expected-head", required=True)
    command.add_argument("--expected-intent-revision", required=True, type=int)
    command.add_argument("--expected-implementer", required=True)
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
