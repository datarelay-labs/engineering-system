#!/usr/bin/env python3
"""No-network, disposable-Git integration tests for terminal lifecycle."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from worktree_lifecycle import reconcile, packet_for_terminal_issue


REPO = "datarelay-labs/engineering-system"


def cmd(*args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, f"{args}: {p.stderr or p.stdout}"
    return p.stdout.strip()


def packet(branch: str, sha: str, wt: Path, *, status="COMPLETE", repo=REPO) -> str:
    return f"""PACKET_VERSION=3
TARGET_REPO={repo}
WORKSTREAM=terminal-lifecycle-regression
STATUS={status}
BRANCH={branch}
WORKTREE={wt}
TASK_KIND=DEVELOPMENT
OWNER_INTENT=Test a bounded safe finalization.
LAST_VERIFIED_HEAD={sha}
INTENT_REVISION=1
CHANGE_RISK=LOW
EXECUTION_PROFILE=datarelay-managed
EXECUTION_PROFILE_REVISION=3

## Goal

Prove cleanup is safe.

## Current State

Merged exact source.

## Next Action

Complete.

## Blockers

NONE
"""


class Fixture:
    def __init__(self, tmp: str):
        self.root = Path(tmp) / "repo"
        self.wt = Path(tmp) / "feature"
        cmd("git", "init", "-q", "-b", "main", str(self.root))
        cmd("git", "-C", str(self.root), "config", "user.email", "test@example.invalid")
        cmd("git", "-C", str(self.root), "config", "user.name", "Test")
        (self.root / "a.txt").write_text("main")
        cmd("git", "-C", str(self.root), "add", ".")
        cmd("git", "-C", str(self.root), "commit", "-qm", "base")
        cmd("git", "-C", str(self.root), "remote", "add", "origin", f"https://github.com/{REPO}.git")
        cmd("git", "-C", str(self.root), "worktree", "add", "-q", "-b", "feat/cleanup", str(self.wt), "HEAD")
        (self.wt / "change.txt").write_text("feature")
        cmd("git", "-C", str(self.wt), "add", ".")
        cmd("git", "-C", str(self.wt), "commit", "-qm", "feature")
        self.head = cmd("git", "-C", str(self.wt), "rev-parse", "HEAD")
        self.issue = {
            "state": "open",
            "number": 123,
            "body": packet("feat/cleanup", self.head, self.wt),
            "user": {"login": "trusted-owner"},
        }
        self.pr = {
            "state": "closed", "merged": True,
            "head": {"sha": self.head, "ref": "feat/cleanup", "repo": {"full_name": REPO}},
            "merge_commit_sha": "b" * 40,
        }
        self.extra = []
        self.writes = []

    def api(self, path, *, payload=None):
        if path.endswith("/pulls/999"):
            return self.pr
        if path.endswith("/issues/123"):
            if payload is not None:
                self.writes.append(payload.copy())
                self.issue["state"] = "closed" if payload.get("state") == "closed" else self.issue["state"]
            return self.issue
        if path.endswith("/collaborators/trusted-owner/permission"):
            return {"permission": "admin"}
        if path.endswith("/issues?state=open&per_page=100"):
            return [self.issue] + list(self.extra)
        raise AssertionError("Unexpected API path " + path)

    def plan(self, *, apply=False, digest=None, processes=lambda _: []):
        return reconcile(
            root=self.root, target=self.wt,
            repo=REPO, pr_number=999, issue_number=123, head=self.head,
            api=self.api, processes=processes,
            apply=apply, expected_plan_sha256=digest,
        )


def test_merged_complete_plan_and_explicit_apply():
    with tempfile.TemporaryDirectory() as d:
        f = Fixture(d)
        planned = f.plan()
        assert planned["decision"] == "ALLOW" and len(planned["plan_sha256"]) == 64
        assert f.wt.exists() and f.issue["state"] == "open" and not f.writes
        missing_digest = f.plan(apply=True)
        assert missing_digest == {"decision": "BLOCK", "reason": "PLAN_DIGEST_MISMATCH"}
        assert f.wt.exists()
        applied = f.plan(apply=True, digest=planned["plan_sha256"])
        assert applied["decision"] == "REMOVED" and applied["issue_state"] == "closed"
        assert not f.wt.exists() and f.writes == [{"state": "closed", "state_reason": "completed"}]
        # Core Git repository and feature commit are not deleted.
        assert f.root.exists()
        assert cmd("git", "-C", str(f.root), "cat-file", "-t", f.head) == "commit"


def test_dirty_untracked_are_blocked():
    with tempfile.TemporaryDirectory() as d:
        f = Fixture(d)
        (f.wt / "hidden.tmp").write_text("owner work")
        p = f.plan()
        assert p["reason"] == "DIRTY_OR_UNTRACKED_WORKTREE", p
        (f.wt / "hidden.tmp").unlink()
        (f.wt / "change.txt").write_text("edited")
        assert f.plan()["reason"] == "DIRTY_OR_UNTRACKED_WORKTREE"
        assert f.issue["state"] == "open"


def test_open_pr_stale_head_and_untrusted_author_block():
    with tempfile.TemporaryDirectory() as d:
        f = Fixture(d)
        f.pr["merged"] = False
        assert f.plan()["reason"] == "PR_NOT_MERGED"
        f.pr["merged"] = True
        f.pr["head"]["sha"] = "c" * 40
        assert f.plan()["reason"] == "PR_SOURCE_NOT_EXACT"
        f.pr["head"]["sha"] = f.head
        f.issue["user"]["login"] = "untrusted"
        # Explicitly provide the untrusted author verdict (not a guessed permission).
        original = f.api
        def untrusted(path, *, payload=None):
            if "/collaborators/" in path:
                return {"permission": "read"}
            if path.endswith("/issues/123"):
                return f.issue
            return original(path, payload=payload)
        f.api = untrusted
        assert f.plan()["reason"] == "ISSUE_AUTHOR_NO_WRITE_AUTHORITY"


def test_issue_paused_ambiguous_or_foreign_blocks():
    with tempfile.TemporaryDirectory() as d:
        f = Fixture(d)
        f.issue["body"] = packet("feat/cleanup", f.head, f.wt, status="PAUSED")
        assert f.plan()["reason"] == "ISSUE_NOT_EXACT_COMPLETE_PACKET"
        f.issue["body"] = packet("feat/cleanup", f.head, f.wt, repo="other/repo")
        assert f.plan()["reason"] == "ISSUE_NOT_EXACT_COMPLETE_PACKET"
        f.issue["body"] = (
            "<!-- ENGINEERING_CURRENT_PACKET_BEGIN -->\n" +
            packet("feat/cleanup", f.head, f.wt) +
            "<!-- ENGINEERING_CURRENT_PACKET_END -->\n" +
            "<!-- ENGINEERING_CURRENT_PACKET_BEGIN -->\n" +
            packet("feat/cleanup", f.head, f.wt) +
            "<!-- ENGINEERING_CURRENT_PACKET_END -->\n"
        )
        assert f.plan()["reason"] == "ISSUE_PACKET_MARKERS_AMBIGUOUS"
        assert f.wt.exists()


def test_active_worker_and_other_open_packet_blocks():
    with tempfile.TemporaryDirectory() as d:
        f = Fixture(d)
        assert f.plan(processes=lambda _: [777])["reason"] == "ACTIVE_PROCESS_REFERENCES_WORKTREE"
        f.extra.append({"number": 124, "body": f"STATUS=ACTIVE\nWORKTREE={f.wt}\n"})
        assert f.plan()["reason"] == "OTHER_OPEN_PACKET_REFERENCES_WORKTREE"
        f.extra.clear()
        f.extra.extend({"number": n, "body": ""} for n in range(200, 300))
        assert f.plan()["reason"] == "OPEN_ISSUE_INVENTORY_INCOMPLETE"


def test_partial_issue_close_preserves_new_worker_files():
    with tempfile.TemporaryDirectory() as d:
        f = Fixture(d)
        planned = f.plan()
        original = f.api
        def worker_starts_after_issue_close(path, *, payload=None):
            result = original(path, payload=payload)
            if payload is not None:
                (f.wt / "worker-untracked.tmp").write_text("must survive")
            return result
        f.api = worker_starts_after_issue_close
        applied = f.plan(apply=True, digest=planned["plan_sha256"])
        assert applied["decision"] == "PARTIAL", applied
        assert applied["issue_closed"] and applied["worktree_preserved"]
        assert f.issue["state"] == "closed" and (f.wt / "worker-untracked.tmp").exists()


def test_symlink_and_different_worktree_block():
    with tempfile.TemporaryDirectory() as d:
        f = Fixture(d)
        alias = Path(d) / "alias"
        alias.symlink_to(f.wt, target_is_directory=True)
        result = reconcile(
            root=f.root, target=alias, repo=REPO, pr_number=999, issue_number=123,
            head=f.head, api=f.api, processes=lambda _: [], apply=False,
        )
        assert result["decision"] == "BLOCK" and result["reason"] == "SYMLINK_WORKTREE_UNSAFE"


def test_complete_marked_packet_parse():
    source = packet("feat/cleanup", "a" * 40, Path("/tmp/test-cleanup"))
    marked = (
        "<!-- ENGINEERING_CURRENT_PACKET_BEGIN -->\n" + source +
        "<!-- ENGINEERING_CURRENT_PACKET_END -->\n"
        "## Historical evidence\n\nSTATUS=ACTIVE\n"
    )
    assert packet_for_terminal_issue(marked).metadata["STATUS"] == "COMPLETE"


if __name__ == "__main__":
    test_merged_complete_plan_and_explicit_apply()
    test_dirty_untracked_are_blocked()
    test_open_pr_stale_head_and_untrusted_author_block()
    test_issue_paused_ambiguous_or_foreign_blocks()
    test_active_worker_and_other_open_packet_blocks()
    test_symlink_and_different_worktree_block()
    test_partial_issue_close_preserves_new_worker_files()
    test_complete_marked_packet_parse()
    print("WORKTREE_LIFECYCLE_TESTS=PASS")
