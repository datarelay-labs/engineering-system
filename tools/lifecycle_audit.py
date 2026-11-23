#!/usr/bin/env python3
"""Read-only GitHub Work Packet and Worktree lifecycle reconciliation.

Uses authenticated GitHub CLI for --github-repo, and never closes or deletes.
A COMPLETE Issue whose exact source PR was CLOSED UNMERGED is *not* a
merged-source cleanup candidate. Legacy --facts semantics remain supported.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from worktree_lifecycle import packet_for_terminal_issue, worker_processes, git, GITHUB_ORIGIN

TERMINAL = {"COMPLETE"}
WAITING = {"PAUSED", "BLOCKED"}
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def audit(payload):
    issues = payload.get("issues")
    if not isinstance(issues, list):
        raise ValueError("issues must be a list")
    pulls = payload.get("pull_requests")
    if pulls is not None and not isinstance(pulls, list):
        raise ValueError("pull_requests must be a list")
    rows, active, seen = [], [], {}
    for raw in issues:
        if not isinstance(raw, dict):
            raise ValueError("issue must be an object")
        number = raw.get("number")
        state = str(raw.get("state", "")).upper()
        body = raw.get("body", "")
        if not isinstance(number, int) or state not in {"OPEN", "CLOSED"} or not isinstance(body, str):
            raise ValueError("invalid issue facts")
        try:
            packet = packet_for_terminal_issue(body)
        except Exception:
            rows.append({"number": number, "classification": "NOT_PACKET"})
            continue
        if "PACKET_VERSION" not in packet.metadata:
            rows.append({"number": number, "classification": "NOT_PACKET"})
            continue
        status = packet.metadata.get("STATUS", "")
        workstream = packet.metadata.get("WORKSTREAM", "")
        branch = packet.metadata.get("BRANCH", "")
        head = packet.metadata.get("LAST_VERIFIED_HEAD", "")
        key = (packet.metadata.get("TARGET_REPO", ""), workstream)
        classification = "HEALTHY"
        exact_pr = None
        if state == "OPEN" and status in TERMINAL:
            classification = "STALE_OPEN_COMPLETE"
            if pulls is not None:
                matches = [
                    p for p in pulls if isinstance(p, dict)
                    and (p.get("head") or {}).get("ref") == branch
                    and (p.get("head") or {}).get("sha") == head
                ]
                if len(matches) > 1:
                    classification = "OPEN_COMPLETE_AMBIGUOUS_PR"
                elif not matches:
                    classification = "OPEN_COMPLETE_SOURCE_UNVERIFIED"
                else:
                    exact_pr = matches[0].get("number")
                    if matches[0].get("merged_at"):
                        classification = "OPEN_COMPLETE_MERGED_SOURCE"
                    elif matches[0].get("state") == "closed":
                        classification = "OPEN_COMPLETE_CLOSED_UNMERGED"
                    else:
                        classification = "OPEN_COMPLETE_OPEN_PR"
        elif state == "CLOSED" and status == "ACTIVE":
            classification = "CLOSED_ACTIVE_CONTRADICTION"
        elif state == "OPEN" and status == "ACTIVE":
            active.append(number)
        elif state == "OPEN" and status in WAITING:
            classification = "HEALTHY_DEFERRED"
        if key in seen and workstream:
            classification = "DUPLICATE_WORKSTREAM_CANDIDATE"
        elif workstream:
            seen[key] = number
        rows.append({
            "number": number, "classification": classification,
            "status": status, "workstream": workstream, "branch": branch,
            "source_pr": exact_pr,
        })
    unfinished = [
        r["number"] for r in rows
        if r["classification"].startswith(("STALE_OPEN_COMPLETE", "OPEN_COMPLETE_"))
    ]
    summary = {
        "open_complete": unfinished,
        "close_candidates": [
            r["number"] for r in rows if r["classification"] == "OPEN_COMPLETE_MERGED_SOURCE"
        ],
        "protected_complete": [
            r["number"] for r in rows if r["classification"] in {
                "OPEN_COMPLETE_AMBIGUOUS_PR",
                "OPEN_COMPLETE_SOURCE_UNVERIFIED",
                "OPEN_COMPLETE_CLOSED_UNMERGED",
                "OPEN_COMPLETE_OPEN_PR",
            }
        ],
        "contradictions": [
            r["number"] for r in rows if r["classification"] == "CLOSED_ACTIVE_CONTRADICTION"
        ],
        "duplicate_candidates": [
            r["number"] for r in rows if r["classification"] == "DUPLICATE_WORKSTREAM_CANDIDATE"
        ],
        "active_packets": active,
    }
    summary["needs_reconciliation"] = any(summary[k] for k in (
        "open_complete", "contradictions", "duplicate_candidates"
    ))
    return {
        "status": "RECONCILE" if summary["needs_reconciliation"] else "PASS",
        "summary": summary, "issues": rows,
        "MUTATION_AUTHORITY": "NO",
    }


def default_github_api(path: str):
    p = subprocess.run(
        ["/usr/bin/gh", "api", path], capture_output=True, text=True, timeout=24
    )
    if p.returncode:
        raise ValueError(f"GITHUB_API_FAILED:{p.returncode}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("GITHUB_API_JSON_INVALID") from exc


def fetch_pages(repo: str, resource: str, api: Callable = default_github_api) -> list:
    if not REPO.fullmatch(repo):
        raise ValueError("REPO_INVALID")
    rows = []
    for page in range(1, 11):
        part = api(f"repos/{repo}/{resource}&page={page}")
        if not isinstance(part, list):
            raise ValueError("GITHUB_PAGE_INVALID")
        rows.extend(part)
        if len(part) < 100:
            return rows
    raise ValueError("GITHUB_PAGINATION_INCOMPLETE")


def live_audit(repo: str, api: Callable = default_github_api):
    issues = fetch_pages(repo, "issues?state=open&per_page=100", api)
    prs = fetch_pages(repo, "pulls?state=all&per_page=100", api)
    source = {
        "issues": [
            {"number": x["number"], "state": x["state"], "body": x.get("body") or ""}
            for x in issues if isinstance(x, dict) and not x.get("pull_request")
        ],
        "pull_requests": prs,
    }
    result = audit(source)
    result["repo"] = repo
    result["open_pr_count"] = sum(p.get("state") == "open" for p in prs)
    return result, source


def scan_worktrees(root: Path, repo: str, source: dict) -> list[dict]:
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise ValueError("ROOT_INVALID")
    origin = git(root, "remote", "get-url", "origin")
    m = GITHUB_ORIGIN.fullmatch(origin)
    if not m or m.group(1).lower() != repo.lower():
        raise ValueError("GIT_REPOSITORY_MISMATCH")
    entries = git(root, "worktree", "list", "--porcelain").split("\n\n")
    prs = source["pull_requests"]
    open_prs = [x for x in prs if x.get("state") == "open"]
    issues = source["issues"]
    results = []
    for block in entries:
        lines = block.splitlines()
        if not lines or not lines[0].startswith("worktree "):
            continue
        path = Path(lines[0][9:])
        if path == root:
            continue
        detail = dict(x.split(" ", 1) if " " in x else (x, "") for x in lines)
        reason = None
        branch = detail.get("branch", "").removeprefix("refs/heads/")
        head = detail.get("HEAD", "")
        matches = [
            p for p in prs if p.get("merged_at")
            and (p.get("head") or {}).get("ref") == branch
            and (p.get("head") or {}).get("sha") == head
            and ((p.get("head") or {}).get("repo") or {}).get("full_name", "").lower() == repo.lower()
        ]
        if not path.is_dir() or path.is_symlink() or path.resolve() != path or not branch:
            reason = "UNAVAILABLE_OR_DETACHED"
        elif "locked" in detail:
            reason = "LOCKED"
        elif git(path, "status", "--porcelain=v1", "--untracked-files=all"):
            reason = "DIRTY_OR_UNTRACKED"
        elif len(matches) != 1:
            reason = "MERGED_PR_EXACT_HEAD_NOT_UNIQUE"
        elif any((p.get("head") or {}).get("ref") == branch for p in open_prs):
            reason = "OPEN_PR_REFERENCES"
        elif any(
            str(path) in i.get("body", "")
            or f"BRANCH={branch}" in i.get("body", "")
            for i in issues
        ):
            reason = "OPEN_ISSUE_REFERENCES"
        elif worker_processes(path):
            reason = "ACTIVE_PROCESS"
        results.append({
            "worktree": str(path), "branch": branch, "head": head,
            "merged_pr": matches[0].get("number") if len(matches) == 1 else None,
            "classification": "CANDIDATE_REVIEW" if reason is None else "PRESERVE",
            "reason": reason or "VERIFY_FRESH_FACTS_BEFORE_EXPLICIT_CLEANUP",
        })
    return results


def main():
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--facts", type=Path)
    src.add_argument("--github-repo")
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args()
    try:
        if args.facts is not None:
            result = audit(json.loads(args.facts.read_text()))
            if args.repo_root is not None:
                raise ValueError("GITHUB_REPO_REQUIRED_FOR_WORKTREE_SCAN")
        else:
            result, facts = live_audit(args.github_repo)
            if args.repo_root is not None:
                result["worktrees"] = scan_worktrees(args.repo_root, args.github_repo, facts)
                result["candidate_review_count"] = sum(
                    x["classification"] == "CANDIDATE_REVIEW" for x in result["worktrees"]
                )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "BLOCK", "reason": str(exc)}, sort_keys=True))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 2 if result["status"] == "RECONCILE" else 0


if __name__ == "__main__":
    sys.exit(main())
