#!/usr/bin/env python3
"""Fail-closed next-chat handoff verifier; strict mode reads the actual GitHub Issue."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from context_epoch import analyze_packet, parse_packet
from work_admission import packet_not_runnable_reason

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _block(reason: str, **extra: object) -> dict:
    return {"status": "BLOCK", "reason": reason, **extra}


def _terminal_first_action(action: str) -> bool:
    """Reject narrowly identifiable completed/status prose as an execution step.

    This is not an NLP substitute for owner-intent review. In particular,
    imperative actions such as 'Verify the fix' remain valid.
    """
    text = re.sub(r"[*_`]", "", action).strip()
    text = re.sub(r"^[0-9]+[.)]\s*", "", text)
    if re.match(r"^(?:NONE|N/A|NO ACTION|NO REMAINING|WAITING|PAUSED|COMPLETE|COMPLETED)\b", text, re.I):
        return True
    return bool(re.match(
        r"^(?:This|The|Previous|Current|Already|Completed|Done)\b.{0,250}?"
        r"\b(?:is|was|has been|have been|are|were)\s+"
        r"(?:already\s+)?(?:fixed|completed?|done|waiting|paused)\b",
        text, re.I,
    ))


def verify(facts: dict) -> dict:
    """Legacy pure verifier: its digest is caller-reported, not a GitHub read."""
    required = ("target_repo", "packet_body", "persisted_body_sha256", "continuation_token")
    missing = [key for key in required if not facts.get(key)]
    if missing:
        return _block("MISSING_FACTS", missing=missing)
    body = str(facts["packet_body"])
    digest = hashlib.sha256(body.encode()).hexdigest()
    persisted = str(facts["persisted_body_sha256"])
    if not SHA_RE.fullmatch(persisted) or digest != persisted:
        return _block("PERSISTED_PACKET_MISMATCH")
    packet = parse_packet(body)
    lint = analyze_packet(packet, expected_target_repo=str(facts["target_repo"]))
    if lint["status"] != "PASS":
        return _block("PACKET_NOT_CLEAN", blocking=lint["blocking"], warnings=lint["warnings"])
    if packet.metadata.get("STATUS") == "ACTIVE" and packet_not_runnable_reason(packet):
        return _block("ACTIVE_PACKET_NOT_RUNNABLE")
    token = str(facts["continuation_token"]).strip()
    if not token or "\n" in token or len(token) > 120:
        return _block("CONTINUATION_TOKEN_INVALID")
    return {"status": "PASS", "reason": "LOCAL_PACKET_VERIFIED_ONLY",
            "evidence_scope": "CALLER_SUPPLIED_PACKET", "continuation_token": token}


def verify_github_issue(facts: dict, issue_number: int, issue: dict) -> dict:
    """Strict handoff: an authenticated GitHub API read must supply *Issue body*.

    A NEXT_CHAT_START comment, proposed local packet, or matching local digest
    cannot substitute for the actual authoritative Issue body.
    """
    if not isinstance(facts, dict) or not isinstance(issue, dict):
        return _block("GITHUB_ISSUE_INVALID")
    repo = facts.get("target_repo")
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        return _block("TARGET_REPO_INVALID")
    if not isinstance(issue_number, int) or issue_number < 1:
        return _block("GITHUB_ISSUE_INVALID")
    url = f"https://github.com/{repo}/issues/{issue_number}"
    if issue.get("number") != issue_number or issue.get("html_url") != url or "pull_request" in issue:
        return _block("GITHUB_ISSUE_IDENTITY_MISMATCH")
    body = issue.get("body")
    if not isinstance(body, str) or body != facts.get("packet_body"):
        return _block("PERSISTED_PACKET_MISMATCH")
    # The digest must be calculated from the authenticated *persisted* Issue.
    if hashlib.sha256(body.encode()).hexdigest() != facts.get("persisted_body_sha256"):
        return _block("PERSISTED_PACKET_MISMATCH")
    required = ("expected_workstream", "expected_owner_intent",
                "expected_intent_revision", "verified_head", "first_action")
    missing = [key for key in required if facts.get(key) is None or facts.get(key) == ""]
    if missing:
        return _block("RESUME_ANCHOR_MISSING", missing=missing)
    packet = parse_packet(body)
    expected = {
        "WORKSTREAM": str(facts["expected_workstream"]),
        "OWNER_INTENT": str(facts["expected_owner_intent"]),
        "INTENT_REVISION": str(facts["expected_intent_revision"]),
        "LAST_VERIFIED_HEAD": str(facts["verified_head"]),
    }
    if not HEAD_RE.fullmatch(expected["LAST_VERIFIED_HEAD"]):
        return _block("VERIFIED_HEAD_INVALID")
    mismatches = [key for key, value in expected.items()
                  if packet.metadata.get(key) != value]
    if mismatches:
        return _block("RESUME_ANCHOR_STALE", mismatches=mismatches)
    if packet.metadata.get("STATUS") == "ACTIVE" and issue.get("state") != "open":
        return _block("GITHUB_ISSUE_NOT_OPEN")
    action = str(facts["first_action"]).strip()
    action_lines = [line.strip() for line in packet.sections.get("Next Action", "").splitlines() if line.strip()]
    if len(action) > 500 or not action or action not in action_lines:
        return _block("FIRST_ACTION_NOT_PERSISTED")
    if action_lines[0] != action:
        return _block("FIRST_ACTION_NOT_FIRST")
    if _terminal_first_action(action):
        return _block("FIRST_ACTION_NOT_EXECUTABLE")
    if not re.search(rf"(?<![A-Za-z0-9])#{issue_number}(?![A-Za-z0-9])",
                     str(facts.get("continuation_token", ""))):
        return _block("CONTINUATION_TOKEN_NOT_BOUND")
    result = verify(facts)
    if result["status"] != "PASS":
        return result
    return {**result, "reason": "HANDOFF_TRANSACTION_VERIFIED",
            "evidence_scope": "AUTHENTICATED_GITHUB_ISSUE",
            "issue_url": url, "workstream": expected["WORKSTREAM"],
            "verified_first_action": action}


def read_github_issue(repo: str, issue_number: int) -> dict:
    """Read only one explicit GitHub Issue via an authenticated CLI."""
    if not REPO_RE.fullmatch(repo):
        raise ValueError("TARGET_REPO_INVALID")
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{issue_number}"],
        text=True, capture_output=True, timeout=20, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh issue read exit={proc.returncode}: {proc.stderr.strip()[:240]}")
    issue = json.loads(proc.stdout)
    if not isinstance(issue, dict):
        raise ValueError("GITHUB_ISSUE_INVALID")
    author = issue.get("user")
    login = author.get("login") if isinstance(author, dict) else None
    if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", login):
        raise ValueError("WORK_PACKET_AUTHOR_UNTRUSTED")
    # The GitHub association/Issue title alone cannot grant execution authority.
    perm = subprocess.run(
        ["gh", "api", f"repos/{repo}/collaborators/{login}/permission"],
        text=True, capture_output=True, timeout=20, check=False,
    )
    if perm.returncode != 0:
        raise RuntimeError(f"gh author permission read exit={perm.returncode}: {perm.stderr.strip()[:240]}")
    permission = json.loads(perm.stdout)
    if not isinstance(permission, dict) or permission.get("permission") not in ("admin", "maintain", "write"):
        raise ValueError("WORK_PACKET_AUTHOR_UNTRUSTED")
    return issue


def resolve_issue_number(facts: dict) -> int | None:
    """A continuation token must pin exactly one unambiguous Work Packet Issue."""
    if not isinstance(facts, dict):
        return None
    token = facts.get("continuation_token")
    if not isinstance(token, str) or "\n" in token or len(token) > 120:
        return None
    refs = re.findall(r"(?<![A-Za-z0-9])#([1-9][0-9]{0,8})(?![A-Za-z0-9])", token)
    if len(refs) != 1:
        return None
    issue_number = int(refs[0])
    pinned = facts.get("work_packet_issue_number")
    if pinned is not None and (type(pinned) is not int or pinned != issue_number):
        return None
    return issue_number


def _git_read(worktree: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", worktree, *args], text=True, capture_output=True,
        timeout=15, check=False,
    )
    if proc.returncode != 0:
        raise ValueError("RESUME_GIT_READ_FAILED")
    return proc.stdout.strip()


def probe_worktree(worktree: str) -> dict:
    """Read-only exact worktree identity; never writes or cleans dirty changes."""
    root = Path(worktree).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("RESUME_WORKTREE_NOT_DIRECTORY")
    top = Path(_git_read(str(root), "rev-parse", "--show-toplevel")).resolve(strict=True)
    if root != top:
        raise ValueError("RESUME_WORKTREE_ROOT_MISMATCH")
    head = _git_read(str(root), "rev-parse", "HEAD")
    branch = _git_read(str(root), "rev-parse", "--abbrev-ref", "HEAD")
    origin = _git_read(str(root), "remote", "get-url", "origin")
    expected = None
    for prefix in ("https://github.com/", "git@github.com:",
                   "ssh://git@github.com/", "git://github.com/"):
        if origin.startswith(prefix):
            expected = origin[len(prefix):].rstrip("/").removesuffix(".git")
            break
    return {
        "worktree": str(root), "head": head, "branch": branch,
        "remote_repo": expected,
        "dirty": bool(_git_read(str(root), "status", "--porcelain", "--untracked-files=normal")),
    }


def _verify_resume_issue_primary(repo: str, number: int, issue: dict, git_state: dict) -> dict:
    """Resolve one explicitly named Work Packet; never enumerate unrelated Issues."""
    if not REPO_RE.fullmatch(repo) or not isinstance(issue, dict) or not isinstance(git_state, dict):
        return _block("RESUME_INPUT_INVALID")
    url = f"https://github.com/{repo}/issues/{number}"
    if (issue.get("number") != number or issue.get("html_url") != url
            or "pull_request" in issue or issue.get("state") != "open"):
        return _block("RESUME_ISSUE_IDENTITY_INVALID")
    body = issue.get("body")
    if not isinstance(body, str):
        return _block("RESUME_PACKET_MISSING")
    packet = parse_packet(body)
    lint = analyze_packet(packet, expected_target_repo=repo)
    if lint["status"] != "PASS":
        return _block("RESUME_PACKET_NOT_CLEAN", blocking=lint["blocking"],
                      warnings=lint["warnings"])
    if packet.metadata.get("STATUS") != "ACTIVE" or packet_not_runnable_reason(packet):
        return _block("RESUME_PACKET_NOT_RUNNABLE")
    if git_state.get("remote_repo") != repo:
        return _block("RESUME_REPOSITORY_MISMATCH")
    expected_worktree = packet.metadata.get("WORKTREE")
    if expected_worktree and Path(expected_worktree).expanduser().resolve() != Path(str(git_state.get("worktree"))).resolve():
        return _block("RESUME_WORKTREE_MISMATCH")
    if packet.metadata.get("BRANCH") != git_state.get("branch"):
        return _block("RESUME_BRANCH_MISMATCH", expected_branch=packet.metadata.get("BRANCH"))
    recorded = packet.metadata.get("LAST_VERIFIED_HEAD")
    if not isinstance(recorded, str) or not HEAD_RE.fullmatch(recorded):
        return _block("RESUME_HEAD_UNBOUND")
    if recorded != git_state.get("head"):
        return _block("RESUME_HEAD_STALE", recorded_head=recorded, actual_head=git_state.get("head"))
    actions = [line.strip() for line in packet.sections.get("Next Action", "").splitlines() if line.strip()]
    if not actions or len(actions[0]) > 500:
        return _block("RESUME_FIRST_ACTION_MISSING")
    if _terminal_first_action(actions[0]):
        return _block("RESUME_FIRST_ACTION_NOT_EXECUTABLE")
    return {
        "status": "PASS", "reason": "RESUME_FIRST_ACTION_VERIFIED",
        "issue_url": url, "workstream": packet.metadata.get("WORKSTREAM"),
        "first_action": actions[0], "next_action": "\n".join(actions)[:2400],
        "owner_intent": packet.metadata.get("OWNER_INTENT"), "branch": git_state["branch"],
        "head": recorded, "dirty_worktree_preserved": bool(git_state.get("dirty")),
    }



def _resume_reconciliation(repo: str, number: int, issue: dict, git_state: dict) -> list[str]:
    """Collect independent, read-only mismatch classes for one pinned Issue.

    Never infer authority from comments, scan other Issues or rewrite Work
    Packets. The original first error still controls the blocking result.
    """
    if not isinstance(issue, dict) or not isinstance(git_state, dict) or not REPO_RE.fullmatch(repo):
        return ["RESUME_INPUT_INVALID"]
    discrepancies: list[str] = []

    def add(reason: str) -> None:
        if reason not in discrepancies:
            discrepancies.append(reason)

    url = f"https://github.com/{repo}/issues/{number}"
    if (issue.get("number") != number or issue.get("html_url") != url
            or "pull_request" in issue or issue.get("state") != "open"):
        add("RESUME_ISSUE_IDENTITY_INVALID")
    body = issue.get("body")
    if not isinstance(body, str):
        add("RESUME_PACKET_MISSING")
        return discrepancies
    packet = parse_packet(body)
    lint = analyze_packet(packet, expected_target_repo=repo)
    if lint["status"] != "PASS":
        add("RESUME_PACKET_NOT_CLEAN")
    if packet.metadata.get("STATUS") != "ACTIVE" or packet_not_runnable_reason(packet):
        add("RESUME_PACKET_NOT_RUNNABLE")
    if git_state.get("remote_repo") != repo:
        add("RESUME_REPOSITORY_MISMATCH")
    expected_worktree = packet.metadata.get("WORKTREE")
    if expected_worktree and Path(expected_worktree).expanduser().resolve() != Path(str(git_state.get("worktree"))).resolve():
        add("RESUME_WORKTREE_MISMATCH")
    if packet.metadata.get("BRANCH") != git_state.get("branch"):
        add("RESUME_BRANCH_MISMATCH")
    recorded = packet.metadata.get("LAST_VERIFIED_HEAD")
    if not isinstance(recorded, str) or not HEAD_RE.fullmatch(recorded):
        add("RESUME_HEAD_UNBOUND")
    elif recorded != git_state.get("head"):
        add("RESUME_HEAD_STALE")
    actions = [line.strip() for line in packet.sections.get("Next Action", "").splitlines() if line.strip()]
    if not actions or len(actions[0]) > 500:
        add("RESUME_FIRST_ACTION_MISSING")
    elif _terminal_first_action(actions[0]):
        add("RESUME_FIRST_ACTION_NOT_EXECUTABLE")
    return discrepancies


def verify_resume_issue(repo: str, number: int, issue: dict, git_state: dict) -> dict:
    """Return the earliest blocking mismatch and all independently observed gaps."""
    result = _verify_resume_issue_primary(repo, number, issue, git_state)
    if result["status"] == "BLOCK":
        # Do not hide a stale HEAD or completed first task behind an earlier
        # packet-lint warning: a new chat needs the whole repair list.
        return {**result, "reconciliation": _resume_reconciliation(repo, number, issue, git_state)}
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", help="Outgoing handoff facts (authoritative Issue read is mandatory)")
    parser.add_argument("--github-issue", type=int,
                        help="Optional exact Work Packet Issue ID; token must name it too")
    parser.add_argument("--resume-repo", help="Incoming resume: exact owner/repository")
    parser.add_argument("--resume-issue", type=int, help="Incoming resume: one exact Work Packet Issue")
    parser.add_argument("--worktree", help="Incoming resume: exact local product worktree")
    args = parser.parse_args()
    resuming = args.resume_issue is not None or args.resume_repo or args.worktree
    try:
        if resuming:
            if (args.facts or args.github_issue is not None or not args.resume_repo
                    or not args.worktree or not args.resume_issue or args.resume_issue < 1):
                result = _block("RESUME_ARGS_INVALID")
            else:
                issue = read_github_issue(args.resume_repo, args.resume_issue)
                git_state = probe_worktree(args.worktree)
                result = verify_resume_issue(args.resume_repo, args.resume_issue, issue, git_state)
        elif not args.facts:
            result = _block("FACTS_INVALID")
        else:
            facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
            if not isinstance(facts, dict):
                raise ValueError("FACTS_INVALID")
            issue_number = resolve_issue_number(facts)
            # Documented --facts-only CLI must NEVER pass from caller bytes alone.
            if issue_number is None or (
                args.github_issue is not None and issue_number != args.github_issue
            ):
                result = _block("GITHUB_ISSUE_NOT_BOUND")
            else:
                issue = read_github_issue(str(facts.get("target_repo", "")), issue_number)
                result = verify_github_issue(facts, issue_number, issue)
    except ValueError as exc:
        reason = str(exc)
        if reason not in (
            "WORK_PACKET_AUTHOR_UNTRUSTED", "TARGET_REPO_INVALID",
            "GITHUB_ISSUE_INVALID", "FACTS_INVALID", "RESUME_GIT_READ_FAILED",
            "RESUME_WORKTREE_NOT_DIRECTORY", "RESUME_WORKTREE_ROOT_MISMATCH",
        ):
            reason = "RESUME_READ_INVALID" if resuming else "GITHUB_READ_INVALID"
        result = _block(reason, detail=str(exc)[:260])
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        result = _block("RESUME_READ_UNAVAILABLE" if resuming else "GITHUB_READ_UNAVAILABLE",
                        detail=str(exc)[:260])
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
