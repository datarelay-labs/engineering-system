#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
from handoff_contract import (
    verify, verify_github_issue, verify_resume_issue, probe_worktree,
    read_github_issue, main as handoff_main,
)

BODY="""PACKET_VERSION=3
TARGET_REPO=datarelay-labs/demo
WORKSTREAM=demo
STATUS=ACTIVE
BRANCH=fix/demo
TASK_KIND=DEVELOPMENT
OWNER_INTENT=Continue the roadmap.
INTENT_REVISION=1
CHANGE_RISK=LOW
EXECUTION_PROFILE=datarelay-managed
EXECUTION_PROFILE_REVISION=3

## Goal
Finish roadmap.
## Current State
Runnable.
## Next Action
Continue implementation.
## Blockers
None.
"""

def facts(body=BODY,digest=None,token="Demo 계속"):
    return {"target_repo":"datarelay-labs/demo","packet_body":body,
            "persisted_body_sha256":digest or hashlib.sha256(body.encode()).hexdigest(),
            "continuation_token":token}

def test_documented_cli_flag_matches_parser():
    session=(Path(__file__).resolve().parents[1]/"standards"/"SESSION_CONTINUITY.md").read_text()
    assert "handoff_contract.py --request-json" not in session
    assert "handoff_contract.py --facts <facts.json>" in session
    assert "Freshly re-read the authoritative ACTIVE Work Packet" not in session
    assert "accurate canonical lifecycle state" in session


def test_strict_handoff_snapshot():
    """A locally self-consistent hash or next-chat comment cannot replace Issue persistence."""
    head = "0123456789abcdef0123456789abcdef01234567"
    first = "1. Finish the already-started inbox task and its focused tests."
    body = BODY.replace(
        "OWNER_INTENT=Continue the roadmap.",
        "OWNER_INTENT=Finish existing inbox task before selecting unrelated roadmap work.\n"
        f"LAST_VERIFIED_HEAD={head}",
    ).replace(
        "## Next Action\nContinue implementation.",
        "## Next Action\n" + first + "\n2. Only then select the next roadmap workstream.",
    )
    data = facts(body=body, token="Demo 계속 — Work Packet #7")
    data.update(expected_workstream="demo",
                expected_owner_intent="Finish existing inbox task before selecting unrelated roadmap work.",
                expected_intent_revision=1, verified_head=head, first_action=first)
    issue = {"number": 7, "state": "open",
             "html_url": "https://github.com/datarelay-labs/demo/issues/7", "body": body}
    assert verify_github_issue(data, 7, issue)["reason"] == "HANDOFF_TRANSACTION_VERIFIED"
    assert verify_github_issue(data, 7, issue)["evidence_scope"] == "AUTHENTICATED_GITHUB_ISSUE"
    terminal = "1. This bounded legacy queued-approval notification defect is fixed and **WAITING**."
    terminal_body = body.replace(first, terminal)
    terminal_data = facts_with_body({**data, "first_action": terminal}, terminal_body)
    assert verify_github_issue(terminal_data, 7, {**issue, "body": terminal_body})["reason"] == "FIRST_ACTION_NOT_EXECUTABLE"
    second = "2. Only then select the next roadmap workstream."
    assert verify_github_issue({**data, "first_action": second}, 7, issue)["reason"] == "FIRST_ACTION_NOT_FIRST"
    unrelated_first = body.replace("## Next Action\n", "## Next Action\n1. Browse all GitHub Issues again.\n")
    assert verify_github_issue(facts_with_body(data, unrelated_first), 7,
                               {**issue, "body": unrelated_first})["reason"] == "FIRST_ACTION_NOT_FIRST"
    # Old authoritative body still on GitHub; latest comment has the new instructions.
    old = {**issue, "body": BODY}
    assert verify_github_issue(data, 7, old)["reason"] == "PERSISTED_PACKET_MISMATCH"
    assert verify_github_issue({**data, "expected_intent_revision": 2}, 7, issue)["reason"] == "RESUME_ANCHOR_STALE"
    assert verify_github_issue({**data, "expected_owner_intent": "Analyze competitor UI now."}, 7, issue)["reason"] == "RESUME_ANCHOR_STALE"
    assert verify_github_issue({**data, "verified_head": "f"*40}, 7, issue)["reason"] == "RESUME_ANCHOR_STALE"
    assert verify_github_issue({**data, "first_action": "Recheck all issues."}, 7, issue)["reason"] == "FIRST_ACTION_NOT_PERSISTED"
    assert verify_github_issue({**data, "continuation_token": "Demo 계속"}, 7, issue)["reason"] == "CONTINUATION_TOKEN_NOT_BOUND"
    assert verify_github_issue({**data, "continuation_token": "Demo 계속 — Work Packet #70"}, 7, issue)["reason"] == "CONTINUATION_TOKEN_NOT_BOUND"
    assert verify_github_issue(data, 9, issue)["reason"] == "GITHUB_ISSUE_IDENTITY_MISMATCH"
    assert verify_github_issue(data, 7, {**issue, "state": "closed"})["reason"] == "GITHUB_ISSUE_NOT_OPEN"
    assert verify_github_issue({k:v for k,v in data.items() if k!="first_action"}, 7, issue)["reason"] == "RESUME_ANCHOR_MISSING"
    assert verify_github_issue(data, 7, {**issue, "pull_request": {"url":"x"}})["reason"] == "GITHUB_ISSUE_IDENTITY_MISMATCH"
    blocked = body.replace("STATUS=ACTIVE", "STATUS=PAUSED")
    assert verify_github_issue(facts_with_body(data, blocked), 7, {**issue,"body":blocked})["status"] == "PASS"


def facts_with_body(data, body):
    return {**data, "packet_body": body, "persisted_body_sha256": hashlib.sha256(body.encode()).hexdigest()}

def test_authenticated_issue_author_gate():
    issue = {"number": 7, "html_url": "https://github.com/datarelay-labs/demo/issues/7",
             "body": BODY, "user": {"login": "trusted-owner"}}
    commands = []

    def call_gh(argv, **kwargs):
        commands.append(tuple(argv))
        if argv[2].endswith("/issues/7"):
            return subprocess.CompletedProcess(argv, 0, json.dumps(issue), "")
        return subprocess.CompletedProcess(argv, 0, json.dumps({"permission": "write"}), "")

    with patch("handoff_contract.subprocess.run", side_effect=call_gh):
        actual = read_github_issue("datarelay-labs/demo", 7)
        assert actual["body"] == BODY
    assert commands == [
        ("gh", "api", "repos/datarelay-labs/demo/issues/7"),
        ("gh", "api", "repos/datarelay-labs/demo/collaborators/trusted-owner/permission"),
    ]
    for permission in ("read", "triage", "none"):
        def fail_perm(argv, **kwargs):
            payload = issue if argv[2].endswith("/issues/7") else {"permission": permission}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        with patch("handoff_contract.subprocess.run", side_effect=fail_perm):
            try:
                read_github_issue("datarelay-labs/demo", 7)
                raise AssertionError("Read-only Issue author incorrectly trusted")
            except ValueError as exc:
                assert str(exc) == "WORK_PACKET_AUTHOR_UNTRUSTED"


def test_strict_cli_read_and_classification():
    head = "0123456789abcdef0123456789abcdef01234567"
    action = "1. Resume the original work before browsing all Issues."
    body = BODY.replace("OWNER_INTENT=Continue the roadmap.",
                        f"OWNER_INTENT=Resume original work.\nLAST_VERIFIED_HEAD={head}")
    body = body.replace("Continue implementation.", action)
    inputs = facts(body=body, token="Demo 계속 — #7")
    inputs.update(expected_workstream="demo", expected_owner_intent="Resume original work.",
                  expected_intent_revision=1, verified_head=head, first_action=action)
    issue = {"number": 7, "state": "open",
             "html_url": "https://github.com/datarelay-labs/demo/issues/7",
             "body": body}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "facts.json"
        path.write_text(json.dumps(inputs), encoding="utf-8")
        for extra_args in ([], ["--github-issue", "7"]):
            # The documented --facts-only invocation is now strict by default.
            with patch.object(sys, "argv", ["handoff_contract.py", "--facts", str(path), *extra_args]):
                out = StringIO()
                with patch("handoff_contract.read_github_issue", return_value=issue) as read, redirect_stdout(out):
                    assert handoff_main() == 0
                assert read.call_args.args == ("datarelay-labs/demo", 7)
                assert json.loads(out.getvalue())["reason"] == "HANDOFF_TRANSACTION_VERIFIED"
                out = StringIO()
                with patch("handoff_contract.read_github_issue",
                           side_effect=ValueError("WORK_PACKET_AUTHOR_UNTRUSTED")), redirect_stdout(out):
                    assert handoff_main() == 2
                assert json.loads(out.getvalue())["reason"] == "WORK_PACKET_AUTHOR_UNTRUSTED"

        # No Issue reference, ambiguous token, or explicit mismatch: zero remote reads.
        for token, extra_args in (
            ("Demo 계속", []),
            ("Demo 계속 — #7 and #8", []),
            ("Demo 계속 — #70", ["--github-issue", "7"]),
            ("Demo 계속 — #7", ["--github-issue", "70"]),
        ):
            bad = {**inputs, "continuation_token": token}
            path.write_text(json.dumps(bad), encoding="utf-8")
            with patch.object(sys, "argv", ["handoff_contract.py", "--facts", str(path), *extra_args]):
                out = StringIO()
                with patch("handoff_contract.read_github_issue") as read, redirect_stdout(out):
                    assert handoff_main() == 2
                read.assert_not_called()
                assert json.loads(out.getvalue())["reason"] == "GITHUB_ISSUE_NOT_BOUND"
        bad = {**inputs, "work_packet_issue_number": 8}
        path.write_text(json.dumps(bad), encoding="utf-8")
        with patch.object(sys, "argv", ["handoff_contract.py", "--facts", str(path)]):
            out = StringIO()
            with patch("handoff_contract.read_github_issue") as read, redirect_stdout(out):
                assert handoff_main() == 2
            read.assert_not_called()
            assert json.loads(out.getvalue())["reason"] == "GITHUB_ISSUE_NOT_BOUND"


def test_incoming_resume_resolves_one_authoritative_workstream():
    head = "0123456789abcdef0123456789abcdef01234567"
    action = "1. Finish the previously started code review before unrelated tasks."
    body = BODY.replace("OWNER_INTENT=Continue the roadmap.",
                        f"OWNER_INTENT=Finish existing review.\nLAST_VERIFIED_HEAD={head}")
    body = body.replace("Continue implementation.", action)
    issue = {"number": 7, "state": "open",
             "html_url": "https://github.com/datarelay-labs/demo/issues/7", "body": body}
    git_state = {"remote_repo": "datarelay-labs/demo", "worktree": "/tmp/demo-worktree",
                 "branch": "fix/demo", "head": head, "dirty": True}
    result = verify_resume_issue("datarelay-labs/demo", 7, issue, git_state)
    assert result["reason"] == "RESUME_FIRST_ACTION_VERIFIED"
    assert result["first_action"] == action
    assert result["dirty_worktree_preserved"] is True
    terminal = "1. This bounded legacy queued-approval notification defect is fixed and **WAITING**."
    terminal_body = body.replace(action, terminal)
    assert verify_resume_issue("datarelay-labs/demo", 7, {**issue, "body": terminal_body},
                               git_state)["reason"] == "RESUME_FIRST_ACTION_NOT_EXECUTABLE"
    actionable = body.replace(action, "1. Verify the fix with focused tests and preserve other work.")
    assert verify_resume_issue("datarelay-labs/demo", 7, {**issue, "body": actionable},
                               git_state)["reason"] == "RESUME_FIRST_ACTION_VERIFIED"
    assert verify_resume_issue("datarelay-labs/demo", 7, {**issue, "state": "closed"}, git_state)["reason"] == "RESUME_ISSUE_IDENTITY_INVALID"
    assert verify_resume_issue("datarelay-labs/demo", 7, {**issue, "body": body.replace("STATUS=ACTIVE", "STATUS=PAUSED")}, git_state)["reason"] == "RESUME_PACKET_NOT_RUNNABLE"
    assert verify_resume_issue("datarelay-labs/demo", 7, issue, {**git_state, "head": "f"*40})["reason"] == "RESUME_HEAD_STALE"
    assert verify_resume_issue("datarelay-labs/demo", 7, issue, {**git_state, "branch": "main"})["reason"] == "RESUME_BRANCH_MISMATCH"
    assert verify_resume_issue("datarelay-labs/demo", 7, issue, {**git_state, "remote_repo": "elsewhere/demo"})["reason"] == "RESUME_REPOSITORY_MISMATCH"
    assert verify_resume_issue("datarelay-labs/demo", 7,
                               {**issue, "body": body+"\n## Old irrelevant history\nother tasks"},
                               git_state)["reason"] == "RESUME_PACKET_NOT_CLEAN"
    bound = body.replace("BRANCH=fix/demo", "BRANCH=fix/demo\nWORKTREE=/tmp/some-other-worktree")
    assert verify_resume_issue("datarelay-labs/demo", 7, {**issue, "body": bound},
                               git_state)["reason"] == "RESUME_WORKTREE_MISMATCH"
    # The incoming CLI reads one pinned Issue, not a list of unrelated Issues.
    with patch.object(sys, "argv", ["handoff_contract.py", "--resume-repo",
                                    "datarelay-labs/demo", "--resume-issue", "7",
                                    "--worktree", "/tmp/demo-worktree"]):
        out = StringIO()
        with patch("handoff_contract.read_github_issue", return_value=issue) as read, \
             patch("handoff_contract.probe_worktree", return_value=git_state) as probed, \
             redirect_stdout(out):
            assert handoff_main() == 0
        read.assert_called_once_with("datarelay-labs/demo", 7)
        probed.assert_called_once_with("/tmp/demo-worktree")
        assert json.loads(out.getvalue())["first_action"] == action


def test_real_git_worktree_probe_is_read_only():
    with tempfile.TemporaryDirectory() as temp:
        def git(*args):
            subprocess.run(["git", "-C", temp, *args], check=True,
                           capture_output=True, text=True)
        subprocess.run(["git", "init", "-b", "demo", temp], check=True, capture_output=True)
        git("remote", "add", "origin", "https://github.com/datarelay-labs/demo.git")
        path = Path(temp)/"work.txt"
        path.write_text("original", encoding="utf-8")
        git("add", "work.txt")
        git("-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-m", "test fixture")
        clean = probe_worktree(temp)
        assert clean["remote_repo"] == "datarelay-labs/demo"
        assert clean["branch"] == "demo" and clean["dirty"] is False
        path.write_text("original plus uncommitted content", encoding="utf-8")
        dirty = probe_worktree(temp)
        assert dirty["head"] == clean["head"] and dirty["dirty"] is True
        assert path.read_text(encoding="utf-8") == "original plus uncommitted content"


def main():
    test_documented_cli_flag_matches_parser()
    test_strict_handoff_snapshot()
    test_authenticated_issue_author_gate()
    test_strict_cli_read_and_classification()
    test_incoming_resume_resolves_one_authoritative_workstream()
    test_real_git_worktree_probe_is_read_only()
    assert verify(facts())["status"]=="PASS"
    assert verify(facts())["reason"]=="LOCAL_PACKET_VERIFIED_ONLY"
    assert verify(facts())["evidence_scope"]=="CALLER_SUPPLIED_PACKET"
    for state in ("PAUSED", "BLOCKED", "COMPLETE"):
        body=BODY.replace("STATUS=ACTIVE", "STATUS="+state)
        assert verify(facts(body=body))["status"]=="PASS"
    body=BODY.replace("STATUS=ACTIVE", "STATUS=WAITING")
    assert verify(facts(body=body))["reason"]=="PACKET_NOT_CLEAN"
    blocked=BODY.replace("None.", "Waiting for owner approval.")
    assert verify(facts(body=blocked))["reason"]=="ACTIVE_PACKET_NOT_RUNNABLE"
    for state in ("PAUSED","BLOCKED","COMPLETE"):
        assert verify(facts(body=blocked.replace("STATUS=ACTIVE","STATUS="+state)))["status"]=="PASS"
    waiting=BODY.replace("STATUS=ACTIVE","STATUS=ACTIVE"+chr(10)+"QUEUE_STATE=DEPENDENCY_WAIT")
    assert verify(facts(body=waiting))["reason"]=="ACTIVE_PACKET_NOT_RUNNABLE"
    for marker in ("WAITING_FOR_CI", "WAITING_FOR_CI=pending-head", "- WAITING_FOR_DEPENDENCY=core"):
        body=BODY+"## Latest Evidence"+chr(10)+marker+chr(10)
        assert verify(facts(body=body))["reason"]=="ACTIVE_PACKET_NOT_RUNNABLE"
        assert verify(facts(body=body.replace("STATUS=ACTIVE","STATUS=PAUSED")))["status"]=="PASS"
    body=BODY+"## Latest Evidence"+chr(10)+"WAITING_FOR_CI=PASS"+chr(10)
    assert verify(facts(body=body))["status"]=="PASS"
    body=BODY.replace("STATUS=ACTIVE","STATUS=ACTIVE"+chr(10)+"WAITING_FOR=CI")
    assert verify(facts(body=body))["reason"]=="ACTIVE_PACKET_NOT_RUNNABLE"
    body=BODY.replace("STATUS=ACTIVE","STATUS=ACTIVE"+chr(10)+"WAITING_FOR=NONE")
    assert verify(facts(body=body))["status"]=="PASS"
    for field, value in (("WAITING_FOR","NONE"),("WAITING_FOR","NO_WAIT"),("QUEUE_STATE","NOT_WAITING")):
        body=BODY.replace("STATUS=ACTIVE","STATUS=ACTIVE"+chr(10)+field+"="+value)
        assert verify(facts(body=body))["status"]=="PASS"
    for action in ("", "NONE", "N/A", "NONE."):
        body=BODY.replace("Continue implementation.",action)
        assert verify(facts(body=body))["status"]=="BLOCK"
    for fence in (chr(96)*3, "~"*3, chr(96)*4):
        quoted=fence+"text"+chr(10)+"WAITING_FOR_CI=past-head"+chr(10)+fence+chr(10)
        body=BODY+"## Latest Evidence"+chr(10)+quoted
        assert verify(facts(body=body))["status"]=="PASS"
        body+="WAITING_FOR_CI=current-head"+chr(10)
        assert verify(facts(body=body))["reason"]=="ACTIVE_PACKET_NOT_RUNNABLE"
    body=BODY.replace("## Current State","## Current State"+chr(10)+"- WAITING_FOR_CI",1)
    assert verify(facts(body=body))["reason"]=="ACTIVE_PACKET_NOT_RUNNABLE"
    assert verify(facts(body=body.replace("STATUS=ACTIVE","STATUS=PAUSED")))["status"]=="PASS"
    body=BODY.replace("## Current State","## Current State"+chr(10)+chr(96)*3+chr(10)+"WAITING_FOR_CI"+chr(10)+chr(96)*3,1)
    assert verify(facts(body=body))["status"]=="PASS"
    assert verify(facts(digest="0"*64))["reason"]=="PERSISTED_PACKET_MISMATCH"
    assert verify(facts(token="prompt\nwith extra instructions"))["reason"]=="CONTINUATION_TOKEN_INVALID"
    wrong=BODY.replace("datarelay-labs/demo","datarelay-labs/other")
    assert verify(facts(body=wrong,digest=hashlib.sha256(wrong.encode()).hexdigest()))["reason"]=="PACKET_NOT_CLEAN"
    print("HANDOFF_CONTRACT_TESTS=PASS")
    return 0
if __name__=="__main__":
    raise SystemExit(main())
