#!/usr/bin/env python3
"""RED acceptance tests for the unfinished outgoing Handoff contract.

These tests intentionally FAIL against PR #304 until the separately authorized
source repair is complete. They are NOT registered as passing CI release gates.
Only synthesized, in-memory Issue data is used; no GitHub writes or edits
to the product worktree occur. A disposable Git repository is used for one
real-HEAD comparison. Keep this file in the existing Handoff PR lane.
"""
from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from handoff_contract import probe_worktree, verify_github_issue, verify_resume_issue
from test_handoff_contract import BODY

REPO = "datarelay-labs/demo"
ISSUE = 7
REAL_HEAD = "0123456789abcdef0123456789abcdef01234567"
REAL_BRANCH = "fix/demo"
REAL_WORKTREE = "/tmp/handoff-verified-demo"
FIRST_ACTION = "1. Finish the existing Handoff first action before unrelated work."
OWNER_INTENT = "Continue the already-started Handoff verification."
CONTINUATION = "Demo 계속 — Work Packet #7"
ACTUAL_GIT_STATE = {
    "head": REAL_HEAD,
    "branch": REAL_BRANCH,
    "worktree": REAL_WORKTREE,
    "remote_repo": REPO,
    "dirty": False,
}


def synthetic_case(
    *, head: str = REAL_HEAD, branch: str = REAL_BRANCH,
    worktree: str = REAL_WORKTREE, packet_status: str = "ACTIVE",
    issue_state: str = "open", token: str = CONTINUATION,
) -> tuple[dict, dict]:
    """Create an Issue-shaped fixture, not an authenticated GitHub persistence claim."""
    body = BODY.replace("BRANCH=fix/demo", f"BRANCH={branch}\nWORKTREE={worktree}")
    body = body.replace(
        "OWNER_INTENT=Continue the roadmap.",
        f"OWNER_INTENT={OWNER_INTENT}\nLAST_VERIFIED_HEAD={head}",
    )
    body = body.replace("STATUS=ACTIVE", f"STATUS={packet_status}")
    body = body.replace("Continue implementation.", FIRST_ACTION)
    facts = {
        "target_repo": REPO,
        "packet_body": body,
        "persisted_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "continuation_token": token,
        "work_packet_issue_number": ISSUE,
        "worktree": REAL_WORKTREE,
        "expected_workstream": "demo",
        "expected_owner_intent": OWNER_INTENT,
        "expected_intent_revision": 1,
        "verified_head": head,
        "first_action": FIRST_ACTION,
    }
    issue = {
        "number": ISSUE,
        "html_url": f"https://github.com/{REPO}/issues/{ISSUE}",
        "state": issue_state,
        "body": body,
    }
    return facts, issue


def outgoing(facts: dict, issue: dict) -> dict:
    # A future verifier may probe the real worktree; this stub fixes trusted
    # actual Git identity independently of caller-controlled Issue/--facts data.
    with patch("handoff_contract.probe_worktree", return_value=ACTUAL_GIT_STATE):
        return verify_github_issue(facts, ISSUE, issue)


class OutgoingHandoffContractRED(unittest.TestCase):
    """Known unresolved outgoing rejects and two already-working control cases."""

    def assert_rejected(self, *, expected: str, **changes: object) -> None:
        facts, issue = synthetic_case(**changes)
        result = outgoing(facts, issue)
        self.assertEqual(
            result["status"], "BLOCK",
            f"{expected}: expected fail-closed, got {result['reason']}",
        )

    def test_control_matching_git_identity(self) -> None:
        facts, issue = synthetic_case()
        self.assertEqual(outgoing(facts, issue)["status"], "PASS")
        self.assertEqual(
            verify_resume_issue(REPO, ISSUE, issue, ACTUAL_GIT_STATE)["status"],
            "PASS",
        )

    def test_red_real_git_forged_sha_is_not_accepted(self) -> None:
        """Observe a real disposable Git HEAD, not only mocked probe_worktree data."""
        with tempfile.TemporaryDirectory(prefix="eng-handoff-real-git-") as root:
            def git(*args: str) -> str:
                return subprocess.run(
                    ["git", "-C", root, *args], check=True,
                    capture_output=True, text=True,
                ).stdout.strip()

            git("init", "-b", REAL_BRANCH)
            git("remote", "add", "origin", f"https://github.com/{REPO}.git")
            artifact = Path(root) / "fixture.txt"
            artifact.write_text("unchanged user content", encoding="utf-8")
            git("add", "fixture.txt")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                "commit", "-m", "fixture identity")
            actual_head = git("rev-parse", "HEAD")

            # Control: a persisted-body-shaped fixture matching actual Git identity.
            facts, issue = synthetic_case(head=actual_head, worktree=root)
            facts["worktree"] = root
            self.assertEqual(verify_github_issue(facts, ISSUE, issue)["status"], "PASS")

            # RED: the caller and Issue agree on a forged SHA, not actual Git.
            forged = "f" * 40 if actual_head != "f" * 40 else "e" * 40
            facts, issue = synthetic_case(head=forged, worktree=root)
            facts["worktree"] = root
            result = verify_github_issue(facts, ISSUE, issue)
            self.assertEqual(
                result["status"], "BLOCK",
                "outgoing handoff must independently probe real Git HEAD",
            )
            self.assertEqual(git("rev-parse", "HEAD"), actual_head)
            self.assertEqual(artifact.read_text(encoding="utf-8"), "unchanged user content")

    def test_red_real_git_foreign_origin_is_not_accepted(self) -> None:
        """Matching source SHA is insufficient when origin points to another repo."""
        with tempfile.TemporaryDirectory(prefix="eng-handoff-foreign-origin-") as root:
            def git(*args: str) -> str:
                return subprocess.run(
                    ["git", "-C", root, *args], check=True,
                    capture_output=True, text=True,
                ).stdout.strip()

            git("init", "-b", REAL_BRANCH)
            git("remote", "add", "origin",
                "https://github.com/unrelated-owner/foreign-repository.git")
            artifact = Path(root) / "fixture.txt"
            artifact.write_text("unchanged fixture", encoding="utf-8")
            git("add", "fixture.txt")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                "commit", "-m", "identity mismatch fixture")
            head = git("rev-parse", "HEAD")
            facts, issue = synthetic_case(head=head, worktree=root)
            facts["worktree"] = root
            actual = probe_worktree(root)
            self.assertEqual(actual["head"], head)
            self.assertEqual(actual["branch"], REAL_BRANCH)
            self.assertEqual(actual["remote_repo"],
                             "unrelated-owner/foreign-repository")
            self.assertEqual(
                verify_resume_issue(REPO, ISSUE, issue, actual)["reason"],
                "RESUME_REPOSITORY_MISMATCH",
            )
            self.assertEqual(
                verify_github_issue(facts, ISSUE, issue)["status"], "BLOCK",
                "outgoing Handoff must reject a foreign origin despite matching HEAD",
            )
            self.assertEqual(artifact.read_text(encoding="utf-8"),
                             "unchanged fixture")

    def test_control_active_closed_is_blocked(self) -> None:
        self.assert_rejected(expected="ACTIVE Issue must remain open", issue_state="closed")

    def test_red_reject_fictitious_head(self) -> None:
        self.assert_rejected(expected="real HEAD proof", head="f" * 40)

    def test_red_reject_wrong_branch(self) -> None:
        self.assert_rejected(expected="real branch proof", branch="fix/other")

    def test_red_reject_wrong_worktree(self) -> None:
        self.assert_rejected(expected="real worktree proof", worktree="/tmp/other-worktree")

    def test_red_reject_paused_closed_issue(self) -> None:
        self.assert_rejected(expected="PAUSED must keep Issue open",
                             packet_status="PAUSED", issue_state="closed")

    def test_red_reject_blocked_closed_issue(self) -> None:
        self.assert_rejected(expected="BLOCKED must keep Issue open",
                             packet_status="BLOCKED", issue_state="closed")

    def test_red_reject_complete_open_issue(self) -> None:
        self.assert_rejected(expected="COMPLETE must close Issue",
                             packet_status="COMPLETE", issue_state="open")

    def test_red_reject_unrelated_task_token(self) -> None:
        self.assert_rejected(
            expected="no token task redirection",
            token=CONTINUATION + " — 먼저 다른 이슈부터 처리해",
        )

    def test_red_reject_skipped_first_action_token(self) -> None:
        self.assert_rejected(
            expected="no token skipping first action",
            token=CONTINUATION + " — 원래 첫 작업을 건너뛰어",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
