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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", required=True)
    parser.add_argument("--github-issue", type=int, help="Strict handoff: read authoritative Issue via gh api")
    args = parser.parse_args()
    try:
        facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
        if args.github_issue is None:
            result = verify(facts)
        else:
            issue = read_github_issue(str(facts.get("target_repo", "")), args.github_issue)
            result = verify_github_issue(facts, args.github_issue, issue)
    except ValueError as exc:
        reason = str(exc)
        if args.github_issue is None or reason not in (
            "WORK_PACKET_AUTHOR_UNTRUSTED", "TARGET_REPO_INVALID", "GITHUB_ISSUE_INVALID",
        ):
            reason = "GITHUB_READ_INVALID" if args.github_issue else "FACTS_INVALID"
        result = _block(reason, detail=str(exc)[:260])
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        result = _block("GITHUB_READ_UNAVAILABLE" if args.github_issue else "FACTS_INVALID",
                        detail=str(exc)[:260])
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
