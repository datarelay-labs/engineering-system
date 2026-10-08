#!/usr/bin/env python3
"""Deterministic regressions for parallel-work admission and handoff sizing."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from work_admission import (  # noqa: E402
    evaluate_eligible,
    evaluate_disposition,
    evaluate_admit,
    evaluate_release,
    evaluate_size,
    paths_overlap,
)

TOOL = ROOT / "tools" / "work_admission.py"


def claim(
    claim_id: str,
    *,
    repository: str = "datarelay-labs/engineering-system",
    workstream: str = "ws-a",
    intent_revision: int = 1,
    worktree: str = "/tmp/wt-a",
    owned_paths: list[str] | None = None,
    status: str = "ACTIVE",
    shared_runtime: dict | None = None,
    dirty: bool = False,
    unpushed: bool = False,
    ambiguous: bool = False,
) -> dict:
    return {
        "claim_id": claim_id,
        "repository": repository,
        "workstream": workstream,
        "intent_revision": intent_revision,
        "worktree": worktree,
        "owned_paths": owned_paths or [],
        "status": status,
        "shared_runtime": shared_runtime,
        "dirty": dirty,
        "unpushed": unpushed,
        "ambiguous": ambiguous,
    }


def host(result: str = "PASS", exit_code: int = 0) -> dict:
    return {"result": result, "exit_code": exit_code}


def run_cli(command: str, payload: dict) -> tuple[int, dict[str, str]]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "request.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        completed = subprocess.run(
            ["python3", str(TOOL), command, "--request-json", str(path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    fields: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    return completed.returncode, fields


def test_paths_overlap() -> None:
    assert paths_overlap("tools/", "tools/work_admission.py")
    assert paths_overlap("tools/work_admission.py", "tools/")
    assert paths_overlap("standards/SESSION_CONTINUITY.md", "standards/SESSION_CONTINUITY.md")
    assert not paths_overlap("tools/", "standards/")
    assert not paths_overlap("a/b", "a/c")
    # Alias/traversal forms must still detect overlap after canonicalization.
    assert paths_overlap("foo/../tools/x.py", "tools/")
    assert paths_overlap("tools/./work_admission.py", "tools/work_admission.py")


def test_p1b_admission_001_cross_repo_unisolated_shared_runtime() -> None:
    report = evaluate_admit(
        {
            "proposed": claim(
                "c2",
                repository="other-org/other-repo",
                workstream="ws-b",
                worktree="/tmp/wt-b",
                owned_paths=["src/"],
                shared_runtime={"id": "db-main", "isolated": False},
            ),
            "existing_claims": [
                claim(
                    "c1",
                    repository="datarelay-labs/engineering-system",
                    owned_paths=["tools/"],
                    worktree="/tmp/wt-a",
                    shared_runtime={"id": "db-main", "isolated": False},
                )
            ],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report["DECISION"] == "DENY"
    assert report["DENY_CLASS"] == "SHARED_RUNTIME"


def test_p1b_admission_001_empty_ownership_fails_closed() -> None:
    report = evaluate_admit(
        {
            "proposed": claim(
                "c2",
                workstream="ws-b",
                worktree="/tmp/wt-b",
                owned_paths=[],
            ),
            "existing_claims": [
                claim("c1", owned_paths=["tools/"], worktree="/tmp/wt-a"),
            ],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report["DECISION"] == "DENY"
    assert report["DENY_CLASS"] == "INSUFFICIENT_OWNERSHIP"

    report_both_empty = evaluate_admit(
        {
            "proposed": claim(
                "c2",
                workstream="ws-b",
                worktree="/tmp/wt-b",
                owned_paths=[],
            ),
            "existing_claims": [
                claim("c1", owned_paths=[], worktree="/tmp/wt-a"),
            ],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report_both_empty["DENY_CLASS"] == "INSUFFICIENT_OWNERSHIP"


def test_p1b_admission_001_worktree_alias_shared() -> None:
    report = evaluate_admit(
        {
            "proposed": claim(
                "c2",
                workstream="ws-b",
                worktree="/tmp/x/../wt",
                owned_paths=["evals/"],
            ),
            "existing_claims": [
                claim("c1", worktree="/tmp/wt", owned_paths=["tools/"]),
            ],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report["DECISION"] == "DENY"
    assert report["DENY_CLASS"] == "SHARED_WORKTREE"


def test_p1b_admission_001_owned_path_alias_overlap() -> None:
    report = evaluate_admit(
        {
            "proposed": claim(
                "c2",
                workstream="ws-b",
                worktree="/tmp/wt-b",
                owned_paths=["foo/../tools/x.py"],
            ),
            "existing_claims": [
                claim("c1", worktree="/tmp/wt-a", owned_paths=["tools/"]),
            ],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report["DECISION"] == "DENY"
    assert report["DENY_CLASS"] == "OVERLAPPING_PATHS"


def test_admit_allows_independent_second_worker() -> None:
    report = evaluate_admit(
        {
            "proposed": claim(
                "c2",
                workstream="ws-b",
                worktree="/tmp/wt-b",
                owned_paths=["evals/"],
                status="CLAIMED",
            ),
            "existing_claims": [
                claim("c1", owned_paths=["tools/"], worktree="/tmp/wt-a"),
            ],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report["DECISION"] == "ALLOW"
    assert report["EXIT_CODE"] == "0"
    assert report["MUTATES_EXISTING_SESSIONS"] == "NO"


def test_admit_denies_default_wip_limit() -> None:
    report = evaluate_admit(
        {
            "proposed": claim(
                "c2",
                workstream="ws-b",
                worktree="/tmp/wt-b",
                owned_paths=["evals/"],
            ),
            "existing_claims": [claim("c1", owned_paths=["tools/"])],
            "host_resource": host("PASS"),
            "wip_limit": 1,
        }
    )
    assert report["DECISION"] == "DENY"
    assert report["DENY_CLASS"] == "WIP_LIMIT"


def test_admit_denies_shared_worktree() -> None:
    report = evaluate_admit(
        {
            "proposed": claim("c2", workstream="ws-b", worktree="/tmp/shared", owned_paths=["b/"]),
            "existing_claims": [claim("c1", worktree="/tmp/shared", owned_paths=["a/"])],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report["DENY_CLASS"] == "SHARED_WORKTREE"


def test_admit_denies_overlapping_claim_identity() -> None:
    report = evaluate_admit(
        {
            "proposed": claim("c2", worktree="/tmp/wt-b", owned_paths=["b/"]),
            "existing_claims": [claim("c1", worktree="/tmp/wt-a", owned_paths=["a/"])],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report["DENY_CLASS"] == "OVERLAPPING_CLAIM"


def test_admit_denies_stale_intent_revision() -> None:
    report = evaluate_admit(
        {
            "proposed": claim(
                "c2",
                intent_revision=2,
                worktree="/tmp/wt-b",
                owned_paths=["b/"],
            ),
            "existing_claims": [
                claim("c1", intent_revision=1, worktree="/tmp/wt-a", owned_paths=["a/"])
            ],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report["DENY_CLASS"] == "STALE_INTENT_REVISION"


def test_admit_denies_overlapping_paths() -> None:
    report = evaluate_admit(
        {
            "proposed": claim(
                "c2",
                workstream="ws-b",
                worktree="/tmp/wt-b",
                owned_paths=["tools/work_admission.py"],
            ),
            "existing_claims": [
                claim("c1", owned_paths=["tools/"], worktree="/tmp/wt-a"),
            ],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report["DENY_CLASS"] == "OVERLAPPING_PATHS"


def test_admit_denies_shared_runtime_without_isolation() -> None:
    report = evaluate_admit(
        {
            "proposed": claim(
                "c2",
                workstream="ws-b",
                worktree="/tmp/wt-b",
                owned_paths=["b/"],
                shared_runtime={"id": "db-main", "isolated": False},
            ),
            "existing_claims": [
                claim(
                    "c1",
                    owned_paths=["a/"],
                    shared_runtime={"id": "db-main", "isolated": True},
                )
            ],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report["DENY_CLASS"] == "SHARED_RUNTIME"


def test_admit_allows_isolated_shared_runtime() -> None:
    report = evaluate_admit(
        {
            "proposed": claim(
                "c2",
                workstream="ws-b",
                worktree="/tmp/wt-b",
                owned_paths=["b/"],
                shared_runtime={"id": "db-main", "isolated": True},
            ),
            "existing_claims": [
                claim(
                    "c1",
                    owned_paths=["a/"],
                    shared_runtime={"id": "db-main", "isolated": True},
                )
            ],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report["DECISION"] == "ALLOW"


def test_admit_denies_host_budget() -> None:
    report = evaluate_admit(
        {
            "proposed": claim("c1", status="CLAIMED"),
            "existing_claims": [],
            "host_resource": host("BLOCK", 2),
            "wip_limit": 1,
        }
    )
    assert report["DENY_CLASS"] == "HOST_BUDGET"


def test_admit_denies_ambiguous_existing_claim() -> None:
    report = evaluate_admit(
        {
            "proposed": claim("c2", workstream="ws-b", worktree="/tmp/wt-b", owned_paths=["b/"]),
            "existing_claims": [claim("c1", owned_paths=["a/"], ambiguous=True)],
            "host_resource": host("PASS"),
            "wip_limit": 2,
        }
    )
    assert report["DENY_CLASS"] == "AMBIGUOUS_CLAIM"


def test_size_batch_keep_split() -> None:
    batch = evaluate_size(
        {
            "primary_outcome_count": 1,
            "adjacent_share_oracle": True,
            "unrelated_domains": False,
            "distinct_approval_gates": False,
            "unclear_rollback": False,
            "effort_band": "micro",
            "context_budget_ok": True,
            "included_issue_count": 3,
        }
    )
    assert batch["WORK_PACKET_SIZING"] == "BATCH"

    keep = evaluate_size(
        {
            "primary_outcome_count": 1,
            "adjacent_share_oracle": False,
            "unrelated_domains": False,
            "distinct_approval_gates": False,
            "unclear_rollback": False,
            "effort_band": "keep",
            "context_budget_ok": True,
            "included_issue_count": 1,
        }
    )
    assert keep["WORK_PACKET_SIZING"] == "KEEP"

    split = evaluate_size(
        {
            "primary_outcome_count": 2,
            "adjacent_share_oracle": False,
            "unrelated_domains": True,
            "distinct_approval_gates": True,
            "unclear_rollback": False,
            "effort_band": "oversize",
            "context_budget_ok": False,
            "included_issue_count": 1,
        }
    )
    assert split["WORK_PACKET_SIZING"] == "SPLIT"
    assert "multiple primary outcomes" in split["REASON"]


def test_release_and_unsafe_cleanup() -> None:
    releasable = [
        claim("c1", status="COMPLETE", dirty=False, unpushed=False),
    ]
    ok = evaluate_release(
        {"action": "release-claim", "claim_id": "c1", "existing_claims": releasable}
    )
    assert ok["DECISION"] == "ALLOW"

    active = evaluate_release(
        {
            "action": "release-claim",
            "claim_id": "c1",
            "existing_claims": [claim("c1", status="ACTIVE")],
        }
    )
    assert active["DENY_CLASS"] == "ACTIVE_CLAIM"

    unsafe = evaluate_release(
        {
            "action": "cleanup-worktree",
            "claim_id": "c1",
            "existing_claims": [claim("c1", status="COMPLETE", dirty=True)],
        }
    )
    assert unsafe["DENY_CLASS"] == "UNSAFE_CLEANUP"

    safe = evaluate_release(
        {
            "action": "cleanup-worktree",
            "claim_id": "c1",
            "existing_claims": releasable,
        }
    )
    assert safe["DECISION"] == "ALLOW"
    assert safe["MUTATES_EXISTING_SESSIONS"] == "NO"


def test_cli_admit_and_size_roundtrip() -> None:
    code, fields = run_cli(
        "admit",
        {
            "proposed": claim("c1", status="CLAIMED"),
            "existing_claims": [],
            "host_resource": host("WARN"),
            "wip_limit": 1,
        },
    )
    assert code == 0
    assert fields["DECISION"] == "ALLOW"
    assert fields["HOST_RESOURCE_RESULT"] == "WARN"
    assert fields["MUTATES_EXISTING_SESSIONS"] == "NO"

    code, fields = run_cli(
        "size",
        {
            "primary_outcome_count": 1,
            "adjacent_share_oracle": True,
            "unrelated_domains": False,
            "distinct_approval_gates": False,
            "unclear_rollback": False,
            "effort_band": "micro",
            "context_budget_ok": True,
            "included_issue_count": 2,
        },
    )
    assert code == 0
    assert fields["WORK_PACKET_SIZING"] == "BATCH"


def test_source_does_not_mutate_sessions() -> None:
    text = TOOL.read_text(encoding="utf-8")
    for token in ("os.kill", "persist stop", "SIGKILL", "subprocess.call"):
        assert token not in text, token


def eligible_facts() -> dict:
    from test_context_epoch import packet
    return {"body": packet(), "expected_target_repo": "datarelay-labs/engineering-system",
            "profile_root": str(ROOT), "observed_worktree": str(ROOT),
            "observed_branch": "feat/context-epoch-packet-projection", "observed_head": "a" * 40,
            "issue_state": "OPEN",
            "dependencies_ready": True, "waiting_for": []}


def test_runnable_selection_excludes_stale_waiting_and_terminal_packets() -> None:
    facts = eligible_facts()
    assert evaluate_eligible(facts)["DECISION"] == "ALLOW"
    mutations = [
        {"issue_state": "CLOSED"},
        {"observed_branch": "another-branch"},
        {"observed_head": "b" * 40},
        {"observed_worktree": str(ROOT.parent / "another-worktree")},
        {"dependencies_ready": False},
        {"waiting_for": ["exact-head CI"]},
        {"expected_target_repo": "datarelay-labs/datarelay-link"},
    ]
    for state in ("PAUSED", "BLOCKED", "COMPLETE"):
        mutations.append({"body": facts["body"].replace("STATUS=ACTIVE", "STATUS=" + state, 1)})
    mutations.append({"body": facts["body"].replace("EXECUTION_PROFILE_REVISION=3", "EXECUTION_PROFILE_REVISION=2", 1)})
    mutations.append({"body": facts["body"].replace("## Blockers\n\nNONE", "## Blockers\n\nWaiting for approval", 1)})
    for state in ("DEPENDENCY_WAIT", "WAIT_EXACT_HEAD_CI", "DEFERRED", "HUMAN_REQUIRED"):
        mutations.append({"body": facts["body"].replace("STATUS=ACTIVE", "STATUS=ACTIVE\nQUEUE_STATE=" + state, 1)})
    for change in mutations:
        assert evaluate_eligible({**facts, **change})["DECISION"] == "DENY", change
    incomplete = dict(facts)
    del incomplete["observed_head"]
    code, result = run_cli("eligible", incomplete)
    assert code == 3 and result["DECISION"] == "DENY"
    for field, value, expected in (
        ("observed_head", "b" * 40, "STALE_HEAD"),
        ("observed_branch", "another-branch", "BRANCH_MISMATCH"),
        ("observed_worktree", str(ROOT.parent / "another-worktree"), "WORKTREE_MISMATCH"),
    ):
        code, result = run_cli("eligible", {**facts, field: value})
        assert code == 2 and result["DENY_CLASS"] == expected
    for value in ("WAITING_FOR_CI", "WAITING_FOR_CI=pending-head", "- WAITING_FOR_DEPENDENCY=core"):
        body = facts["body"].replace("## Latest Evidence", "## Latest Evidence" + chr(10) + value, 1)
        assert evaluate_eligible({**facts, "body": body})["DENY_CLASS"] == "WAITING"
    for value in ("WAITING_FOR_CI=PASS", "WAITING_FOR_CI=NONE", "Historical WAITING_FOR_CI is resolved"):
        body = facts["body"].replace("## Latest Evidence", "## Latest Evidence" + chr(10) + value, 1)
        assert evaluate_eligible({**facts, "body": body})["DECISION"] == "ALLOW"
    body = facts["body"].replace("STATUS=ACTIVE", "STATUS=ACTIVE"+chr(10)+"WAITING_FOR=CI", 1)
    assert evaluate_eligible({**facts, "body": body})["DENY_CLASS"] == "WAITING"
    body = facts["body"].replace("STATUS=ACTIVE", "STATUS=ACTIVE"+chr(10)+"WAITING_FOR=NONE", 1)
    assert evaluate_eligible({**facts, "body": body})["DECISION"] == "ALLOW"
    for field, value in (("WAITING_FOR","NONE"),("WAITING_FOR","NO_WAIT"),("QUEUE_STATE","NOT_WAITING")):
        body = facts["body"].replace("STATUS=ACTIVE","STATUS=ACTIVE"+chr(10)+field+"="+value,1)
        assert evaluate_eligible({**facts,"body":body})["DECISION"]=="ALLOW"
    for action in ("", "NONE", "N/A", "NONE."):
        body = facts["body"].replace("Implement and validate.", action)
        assert evaluate_eligible({**facts,"body":body})["DECISION"]=="DENY"
    for fence in (chr(96)*3, "~"*3, chr(96)*4):
        quoted = fence+"text"+chr(10)+"WAITING_FOR_CI=past-head"+chr(10)+fence+chr(10)
        body = facts["body"].replace("## Latest Evidence", "## Latest Evidence"+chr(10)+quoted,1)
        assert evaluate_eligible({**facts,"body":body})["DECISION"]=="ALLOW"
        live = body.replace(quoted,quoted+"WAITING_FOR_CI=current-head"+chr(10),1)
        assert evaluate_eligible({**facts,"body":live})["DENY_CLASS"]=="WAITING"
    for section in ("Current State", "Latest Evidence"):
        for marker in ("WAITING_FOR_CI", "- WAITING_FOR_DEPENDENCY=core"):
            body=facts["body"].replace("## "+section,"## "+section+chr(10)+marker,1)
            assert evaluate_eligible({**facts,"body":body})["DENY_CLASS"]=="WAITING"
            body=facts["body"].replace("## "+section,"## "+section+chr(10)+chr(96)*3+chr(10)+marker+chr(10)+chr(96)*3,1)
            assert evaluate_eligible({**facts,"body":body})["DECISION"]=="ALLOW"
    # A CI-waiting lane must not serialize independent ready work.
    reports = [evaluate_eligible({**facts, "waiting_for": ["CI"]}), evaluate_eligible(facts)]
    assert [r["DECISION"] for r in reports] == ["DENY", "ALLOW"]
    # CI mentions in the completion contract are not observed wait conditions.
    body = facts["body"].replace("Implement and validate.", "Implement and validate; run CI at integration.")
    assert evaluate_eligible({**facts, "body": body})["DECISION"] == "ALLOW"
    body = facts["body"].replace("STATUS=ACTIVE", "STATUS=ACTIVE\nQUEUE_STATE=IMPLEMENTATION", 1)
    assert evaluate_eligible({**facts, "body": body})["DECISION"] == "ALLOW"
    code, result = run_cli("eligible", facts)
    assert code == 0 and result["DECISION"] == "ALLOW"
    code, result = run_cli("eligible", {**facts, "waiting_for": ["CI"]})
    assert code == 2 and result["DENY_CLASS"] == "WAITING"


def test_turn_disposition_policy_surfaces() -> None:
    required = (
        "AGENTS.md",
        "templates/AGENTS.md",
        "templates/CHATGPT_PROJECT_INSTRUCTION.txt",
        "templates/CHATGPT_CUSTOM_INSTRUCTION.txt",
        "standards/SESSION_CONTINUITY.md",
        "ai/AGENT_BASE.md",
    )
    for rel in required:
        body = (ROOT / rel).read_text(encoding="utf-8")
        assert "work_admission.py disposition" in body, rel
        assert "optional" in body.lower(), rel
        assert "FINAL_ALLOWED" in body, rel
        assert "only `FINAL_ALLOWED=YES`" not in body, rel
        assert "forbids a final response" not in body, rel
    session = (ROOT / "standards/SESSION_CONTINUITY.md").read_text(encoding="utf-8")
    assert "keep a ChatGPT turn alive" in session
    assert "Unit tests of its classification cannot prove" in session


def test_turn_disposition_classifies_supplied_facts() -> None:
    facts = eligible_facts()

    status = evaluate_disposition({"request_scope": "status-only"})
    assert status["TURN_DISPOSITION"] == "ALLOW_FINAL"
    assert status["FINAL_ALLOWED"] == "YES"

    runnable = evaluate_disposition(
        {
            "request_scope": "repository",
            "runnable_candidates": [facts],
        }
    )
    assert runnable["TURN_DISPOSITION"] == "CONTINUE"
    assert runnable["FINAL_ALLOWED"] == "NO"
    assert runnable["RUNNABLE_CANDIDATE_COUNT"] == "1"
    assert runnable["SCHEDULER_RECONCILED"] == "NO"

    waiting = {**facts, "waiting_for": ["exact-head CI"]}
    mixed = evaluate_disposition(
        {
            "request_scope": "repository",
            "runnable_candidates": [waiting, facts],
        }
    )
    assert mixed["TURN_DISPOSITION"] == "CONTINUE"
    assert mixed["RUNNABLE_CANDIDATE_COUNT"] == "1"

    unreconciled = evaluate_disposition(
        {
            "request_scope": "repository",
            "runnable_candidates": [waiting],
            "scheduler_reconciled": False,
            "remaining_state": "UNKNOWN",
        }
    )
    assert unreconciled["TURN_DISPOSITION"] == "RECONCILE"
    assert unreconciled["FINAL_ALLOWED"] == "NO"

    unknown = evaluate_disposition(
        {
            "request_scope": "repository",
            "runnable_candidates": [],
            "scheduler_reconciled": True,
            "remaining_state": "UNKNOWN",
        }
    )
    assert unknown["TURN_DISPOSITION"] == "RECONCILE"

    for state in ("COMPLETE", "NO_SAFE_RUNNABLE"):
        report = evaluate_disposition(
            {
                "request_scope": "repository",
                "runnable_candidates": [waiting],
                "scheduler_reconciled": True,
                "remaining_state": state,
            }
        )
        assert report["TURN_DISPOSITION"] == "ALLOW_FINAL", state
        assert report["FINAL_ALLOWED"] == "YES"

    for state in ("OWNER_REQUIRED", "IRRECONCILABLE"):
        report = evaluate_disposition(
            {
                "request_scope": "repository",
                "runnable_candidates": [waiting],
                "scheduler_reconciled": True,
                "remaining_state": state,
            }
        )
        assert report["TURN_DISPOSITION"] == "BLOCKED", state
        assert report["FINAL_ALLOWED"] == "YES"

    independent = evaluate_disposition(
        {
            "request_scope": "repository",
            "runnable_candidates": [waiting, facts],
            "scheduler_reconciled": True,
            "remaining_state": "OWNER_REQUIRED",
        }
    )
    assert independent["TURN_DISPOSITION"] == "CONTINUE"
    assert independent["FINAL_ALLOWED"] == "NO"

    code, fields = run_cli(
        "disposition",
        {
            "request_scope": "repository",
            "runnable_candidates": [facts],
        },
    )
    assert code == 0
    assert fields["TURN_DISPOSITION"] == "CONTINUE"
    assert fields["FINAL_ALLOWED"] == "NO"

    code, fields = run_cli(
        "disposition",
        {
            "request_scope": "repository",
            "runnable_candidates": [],
            "scheduler_reconciled": True,
            "remaining_state": "NOT_A_STATE",
        },
    )
    assert code == 3
    assert fields["DECISION"] == "DENY"


def main() -> int:
    test_turn_disposition_policy_surfaces()
    test_turn_disposition_classifies_supplied_facts()
    test_runnable_selection_excludes_stale_waiting_and_terminal_packets()
    test_paths_overlap()
    test_p1b_admission_001_cross_repo_unisolated_shared_runtime()
    test_p1b_admission_001_empty_ownership_fails_closed()
    test_p1b_admission_001_worktree_alias_shared()
    test_p1b_admission_001_owned_path_alias_overlap()
    test_admit_allows_independent_second_worker()
    test_admit_denies_default_wip_limit()
    test_admit_denies_shared_worktree()
    test_admit_denies_overlapping_claim_identity()
    test_admit_denies_stale_intent_revision()
    test_admit_denies_overlapping_paths()
    test_admit_denies_shared_runtime_without_isolation()
    test_admit_allows_isolated_shared_runtime()
    test_admit_denies_host_budget()
    test_admit_denies_ambiguous_existing_claim()
    test_size_batch_keep_split()
    test_release_and_unsafe_cleanup()
    test_cli_admit_and_size_roundtrip()
    test_source_does_not_mutate_sessions()
    print("WORK_ADMISSION_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
