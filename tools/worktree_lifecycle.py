#!/usr/bin/env python3
"""Safely reconcile one COMPLETE GitHub Work Packet and its merged-PR worktree.

Read-only by default. --apply requires the exact plan digest from a prior
read and rechecks all remote/local facts before any Issue or Git mutation.
This is a bounded terminal cleanup tool, never a recursive garbage collector.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from context_epoch import parse_packet
from current_packet_issue import _marker_indices, BEGIN, END

SHA = re.compile(r"^[0-9a-f]{40}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GITHUB_ORIGIN = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


class CleanupBlocked(ValueError):
    """No side effect may occur after a failed preflight."""


def command(*argv: str, timeout: int = 15) -> str:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanupBlocked(f"COMMAND_UNAVAILABLE:{type(exc).__name__}") from exc
    if p.returncode:
        raise CleanupBlocked(f"COMMAND_FAILED:{Path(argv[0]).name}:{p.returncode}")
    return p.stdout.strip()


def git(root: Path, *args: str) -> str:
    return command("/usr/bin/git", "-C", str(root), *args)


def github_api(path: str, *, payload: dict | None = None) -> dict | list:
    if payload is None:
        raw = command("/usr/bin/gh", "api", path, timeout=24)
    else:
        p = subprocess.run(
            ["/usr/bin/gh", "api", "-X", "PATCH", path, "--input", "-"],
            input=json.dumps(payload), capture_output=True, text=True, timeout=24,
        )
        if p.returncode:
            raise CleanupBlocked(f"GITHUB_MUTATION_FAILED:{p.returncode}")
        raw = p.stdout
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CleanupBlocked("GITHUB_RESPONSE_INVALID") from exc


def packet_for_terminal_issue(body: str):
    # Marked active selectors intentionally reject COMPLETE. For terminal
    # evidence, select the same unique unfenced block but parse its final state.
    if "ENGINEERING_CURRENT_PACKET_" in body:
        lines, begins, ends = _marker_indices(body)
        if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
            raise CleanupBlocked("ISSUE_PACKET_MARKERS_AMBIGUOUS")
        content = "".join(lines[begins[0] + 1:ends[0]])
    else:
        content = body
    if len(body.encode("utf-8")) > 1024 * 1024:
        raise CleanupBlocked("ISSUE_TOO_LARGE")
    packet = parse_packet(content)
    if packet.duplicate_metadata or packet.duplicate_sections:
        raise CleanupBlocked("ISSUE_PACKET_DUPLICATE_SECTIONS")
    return packet


def worker_processes(worktree: Path) -> list[int]:
    """Inspect foreign process CWD/argv. Exclude this CLI's own ancestry."""
    ancestors: set[int] = set()
    parent = os.getpid()
    for _ in range(32):
        if parent in ancestors or parent < 2:
            break
        ancestors.add(parent)
        try:
            fields = Path(f"/proc/{parent}/stat").read_text().rsplit(")", 1)[1].split()
            parent = int(fields[1])
        except (OSError, ValueError, IndexError):
            break
    attached: list[int] = []
    prefix = str(worktree) + "/"
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal() or int(entry.name) in ancestors:
            continue
        try:
            cwd = os.readlink(entry / "cwd")
            argv = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace")
        except (OSError, PermissionError):
            continue
        if cwd == str(worktree) or cwd.startswith(prefix) or str(worktree) in argv:
            attached.append(int(entry.name))
    return sorted(attached)


def require_workspace(root: Path, target: Path, head: str) -> str:
    if not root.is_absolute() or not target.is_absolute():
        raise CleanupBlocked("ABSOLUTE_PATHS_REQUIRED")
    if root.is_symlink() or target.is_symlink():
        raise CleanupBlocked("SYMLINK_WORKTREE_UNSAFE")
    try:
        if root.resolve(strict=True) != root or target.resolve(strict=True) != target:
            raise CleanupBlocked("SYMLINK_PARENT_UNSAFE")
    except OSError as exc:
        raise CleanupBlocked("WORKTREE_PATH_UNAVAILABLE") from exc
    # Never remove the current shell/CLI's working directory.
    cwd = Path.cwd()
    if cwd == target or target in cwd.parents:
        raise CleanupBlocked("CALLER_RUNNING_INSIDE_WORKTREE")
    if not root.is_dir() or not target.is_dir() or root == target:
        raise CleanupBlocked("WORKTREE_MISSING_OR_PRIMARY")
    if git(root, "rev-parse", "--path-format=absolute", "--git-common-dir") != git(
        target, "rev-parse", "--path-format=absolute", "--git-common-dir"
    ):
        raise CleanupBlocked("FOREIGN_GIT_REPOSITORY")
    if Path(git(target, "rev-parse", "--show-toplevel")) != target:
        raise CleanupBlocked("WORKTREE_ROOT_MISMATCH")
    entries = git(root, "worktree", "list", "--porcelain").split("\n\n")
    matches = [
        block for block in entries
        if block.splitlines() and block.splitlines()[0] == f"worktree {target}"
    ]
    if len(matches) != 1 or "locked" in matches[0].splitlines():
        raise CleanupBlocked("UNREGISTERED_OR_LOCKED_WORKTREE")
    if git(target, "rev-parse", "HEAD") != head:
        raise CleanupBlocked("WORKTREE_HEAD_DRIFT")
    try:
        branch = git(target, "symbolic-ref", "--quiet", "--short", "HEAD")
    except CleanupBlocked as exc:
        raise CleanupBlocked("DETACHED_WORKTREE_NEEDS_DISTINCT_POLICY") from exc
    if git(target, "status", "--porcelain=v1", "--untracked-files=all"):
        raise CleanupBlocked("DIRTY_OR_UNTRACKED_WORKTREE")
    return branch


def terminal_plan(
    *,
    root: Path, target: Path, repo: str, pr_number: int,
    issue_number: int, head: str,
    api: Callable[..., Any] = github_api,
    processes: Callable[[Path], list[int]] = worker_processes,
) -> dict:
    if not SHA.fullmatch(head) or not REPO.fullmatch(repo):
        raise CleanupBlocked("INVALID_SOURCE_IDENTITY")
    if pr_number < 1 or issue_number < 1:
        raise CleanupBlocked("INVALID_GITHUB_NUMBER")
    root = root.absolute()
    target = target.absolute()
    branch = require_workspace(root, target, head)
    match = GITHUB_ORIGIN.fullmatch(git(root, "remote", "get-url", "origin"))
    if not match or match.group(1).lower() != repo.lower():
        raise CleanupBlocked("ORIGIN_REPO_MISMATCH")

    pr = api(f"repos/{repo}/pulls/{pr_number}")
    issue = api(f"repos/{repo}/issues/{issue_number}")
    if not isinstance(pr, dict) or pr.get("merged") is not True or pr.get("state") != "closed":
        raise CleanupBlocked("PR_NOT_MERGED")
    pr_head = pr.get("head") or {}
    if (
        pr_head.get("sha") != head or pr_head.get("ref") != branch
        or (pr_head.get("repo") or {}).get("full_name", "").lower() != repo.lower()
        or not SHA.fullmatch(pr.get("merge_commit_sha") or "")
    ):
        raise CleanupBlocked("PR_SOURCE_NOT_EXACT")
    if not isinstance(issue, dict) or issue.get("state") not in ("open", "closed"):
        raise CleanupBlocked("ISSUE_STATE_UNKNOWN")
    body = issue.get("body")
    if not isinstance(body, str):
        raise CleanupBlocked("ISSUE_BODY_INVALID")
    packet = packet_for_terminal_issue(body)
    if (
        packet.metadata.get("STATUS") != "COMPLETE"
        or packet.metadata.get("TARGET_REPO", "").lower() != repo.lower()
        or packet.metadata.get("BRANCH") != branch
        or packet.metadata.get("LAST_VERIFIED_HEAD") != head
        or packet.sections.get("Blockers", "").strip().upper() not in {"NONE", "- NONE"}
    ):
        raise CleanupBlocked("ISSUE_NOT_EXACT_COMPLETE_PACKET")
    if packet.metadata.get("WORKTREE") and Path(packet.metadata["WORKTREE"]) != target:
        raise CleanupBlocked("ISSUE_WORKTREE_MISMATCH")
    author = (issue.get("user") or {}).get("login")
    if not isinstance(author, str) or not author:
        raise CleanupBlocked("ISSUE_AUTHOR_UNVERIFIED")
    permission = api(f"repos/{repo}/collaborators/{author}/permission")
    if not isinstance(permission, dict) or permission.get("permission") not in {"write", "maintain", "admin"}:
        raise CleanupBlocked("ISSUE_AUTHOR_NO_WRITE_AUTHORITY")

    # Do not retire a worktree still referenced by a second open Work Packet.
    other = api(f"repos/{repo}/issues?state=open&per_page=100")
    if not isinstance(other, list) or len(other) >= 100:
        raise CleanupBlocked("OPEN_ISSUE_INVENTORY_INCOMPLETE")
    for item in other:
        if not isinstance(item, dict) or item.get("number") == issue_number:
            continue
        other_body = item.get("body") or ""
        if isinstance(other_body, str) and (
            str(target) in other_body or
            (f"BRANCH={branch}" in other_body and "STATUS=ACTIVE" in other_body)
        ):
            raise CleanupBlocked("OTHER_OPEN_PACKET_REFERENCES_WORKTREE")
    if processes(target):
        raise CleanupBlocked("ACTIVE_PROCESS_REFERENCES_WORKTREE")
    snapshot = {
        "repo": repo, "pr": pr_number, "issue": issue_number,
        "head": head, "branch": branch, "worktree": str(target),
        "issue_state": issue["state"], "issue_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "merge_commit": pr["merge_commit_sha"],
    }
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"decision": "ALLOW", "plan_sha256": digest, **snapshot}


def reconcile(*, apply: bool, expected_plan_sha256: str | None = None, **kwargs: Any) -> dict:
    issue_closed = False
    try:
        result = terminal_plan(**kwargs)
        if not apply:
            return result
        if expected_plan_sha256 != result["plan_sha256"]:
            raise CleanupBlocked("PLAN_DIGEST_MISMATCH")
        api = kwargs.get("api", github_api)
        repo, issue = result["repo"], result["issue"]
        if result["issue_state"] == "open":
            api(f"repos/{repo}/issues/{issue}", payload={"state": "closed", "state_reason": "completed"})
            verified = api(f"repos/{repo}/issues/{issue}")
            if not isinstance(verified, dict) or verified.get("state") != "closed":
                raise CleanupBlocked("ISSUE_CLOSE_NOT_VERIFIED")
            issue_closed = True
            if hashlib.sha256(str(verified.get("body", "")).encode()).hexdigest() != result["issue_body_sha256"]:
                raise CleanupBlocked("ISSUE_CHANGED_DURING_CLOSE")
        # In case a worker started after the first plan, re-check Git and
        # process liveness before attempting Git's own non-forced clean removal.
        target = kwargs["target"]
        root = kwargs["root"]
        if require_workspace(root, target, result["head"]) != result["branch"]:
            raise CleanupBlocked("BRANCH_CHANGED_DURING_CLOSE")
        if kwargs.get("processes", worker_processes)(target):
            raise CleanupBlocked("ACTIVE_PROCESS_DURING_CLOSE")
        git(root, "worktree", "remove", str(target))
        if target.exists():
            raise CleanupBlocked("WORKTREE_REMOVE_NOT_VERIFIED")
        return {**result, "decision": "REMOVED", "issue_state": "closed"}
    except CleanupBlocked as exc:
        if issue_closed:
            return {"decision": "PARTIAL", "reason": str(exc),
                    "issue_closed": True, "worktree_preserved": True}
        return {"decision": "BLOCK", "reason": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    args = parser.parse_args()
    result = reconcile(
        root=args.repo_root, target=args.worktree,
        repo=args.repo, pr_number=args.pr, issue_number=args.issue,
        head=args.head, apply=args.apply,
        expected_plan_sha256=args.expected_plan_sha256,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["decision"] in {"ALLOW", "REMOVED"} else 2


if __name__ == "__main__":
    sys.exit(main())
