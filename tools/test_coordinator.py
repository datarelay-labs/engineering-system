#!/usr/bin/env python3
"""Deterministic regressions for the pure coordinator planner."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from coordinator import plan  # noqa: E402

TOOL = ROOT / "tools" / "coordinator.py"
FIXTURES = ROOT / "tools" / "fixtures" / "coordinator"
SCHEMA = json.loads((ROOT / "schemas" / "coordinator-decision.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
SHA = "a" * 40
OTHER = "b" * 40

CASES = (
    ("01-admit-implementation.json", "ADMIT_IMPLEMENTATION", "SEND", True),
    ("02-resume-worker.json", "RESUME_WORKER", "SUPPRESS", False),
    ("03-stale-worker.json", "STALE_WORKER", "SEND", False),
    ("04-audit-dirty-tree.json", "AUDIT_DIRTY_TREE", "SEND", False),
    ("05-authorize-publication.json", "AUTHORIZE_PUBLICATION", "SEND", False),
    ("06-wait-exact-head-ci.json", "WAIT_EXACT_HEAD_CI", "SEND", False),
    ("07-audit-review.json", "AUDIT_REVIEW", "SEND", False),
    ("08-merge-ready.json", "MERGE_READY", "SEND", False),
    ("09-yield-resource.json", "YIELD_RESOURCE", "SEND", False),
    ("10-reconcile-ambiguous.json", "RECONCILE_AMBIGUOUS", "SEND", False),
    ("11-replan-semantic.json", "REPLAN_SEMANTIC_FAILURE", "SEND", False),
    ("12-paused.json", "NOOP_PAUSED", "SEND", False),
    ("12-blocked.json", "BLOCK_HUMAN", "SEND", False),
    ("12-complete.json", "NOOP_COMPLETE", "SEND", False),
    ("13-identical-wait-dedup.json", "WAIT_EXACT_HEAD_CI", "SUPPRESS", False),
)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def assert_schema(decision: dict) -> None:
    errors = sorted(VALIDATOR.iter_errors(decision), key=lambda item: list(item.path))
    assert not errors, errors[0].message


def test_fixtures() -> None:
    for name, expected, notification, launches in CASES:
        decision = plan(load_fixture(name))
        assert_schema(decision)
        assert decision["decision"] == expected, name
        assert decision["notification_disposition"] == notification, name
        assert decision["launches_worker"] is launches, name
        assert decision["stops_unrelated_sessions"] is False
        assert decision["mutates_existing_sessions"] is False
        assert decision["spawns_process"] is False
        assert decision["mutates_github"] is False
        assert decision["sends_notification"] is False
        again = plan(load_fixture(name))
        assert again == decision


def test_identical_wait_suppress_reason() -> None:
    decision = plan(load_fixture("13-identical-wait-dedup.json"))
    assert decision["notification_suppress_reason"] == "IDENTICAL_WAIT"
    assert decision["decision_class"] == "WAIT"


def test_p2_coord_001_liveness_without_progress_does_not_resume() -> None:
    facts = load_fixture("02-resume-worker.json")
    facts["worker"]["progress_evidence"] = False
    decision = plan(facts)
    assert_schema(decision)
    assert decision["decision"] == "WAIT_EXTERNAL"
    assert decision["decision_class"] == "WAIT"
    assert decision["launches_worker"] is False
    assert decision["stops_unrelated_sessions"] is False
    assert decision["spawns_process"] is False
    assert "no machine-observable progress evidence" in decision["reason"]
    assert plan(facts) == decision
    assert plan(load_fixture("02-resume-worker.json"))["decision"] == "RESUME_WORKER"


def test_resume_is_worker_microstep() -> None:
    decision = plan(load_fixture("02-resume-worker.json"))
    assert decision["notification_suppress_reason"] == "WORKER_MICROSTEP"


def test_non_active_never_launch_even_with_worker() -> None:
    for name in ("12-paused.json", "12-blocked.json", "12-complete.json"):
        decision = plan(load_fixture(name))
        assert decision["launches_worker"] is False


def test_priority_does_not_change_decision() -> None:
    facts = load_fixture("01-admit-implementation.json")
    high = plan(facts)
    facts["packet"]["priority"] = "LOW"
    low = plan(facts)
    assert high["decision"] == low["decision"] == "ADMIT_IMPLEMENTATION"
    assert low["priority"] == "LOW"
    assert high["change_risk"] == low["change_risk"]


def test_stale_publication_revision_does_not_authorize() -> None:
    facts = load_fixture("05-authorize-publication.json")
    facts["publication"]["authorized_intent_revision"] = 2
    decision = plan(facts)
    assert decision["decision"] == "AUDIT_DIRTY_TREE"
    assert decision["launches_worker"] is False


def test_stale_ci_head_does_not_merge() -> None:
    facts = load_fixture("08-merge-ready.json")
    facts["ci"]["subject_head"] = OTHER
    decision = plan(facts)
    assert decision["decision"] == "WAIT_EXACT_HEAD_CI"
    assert decision["subject_version"] == SHA


def test_high_risk_merge_requires_audit() -> None:
    facts = load_fixture("08-merge-ready.json")
    facts["publication"] = {"coordinator_audit": "UNKNOWN"}
    decision = plan(facts)
    assert decision["decision"] == "WAIT_EXTERNAL"
    facts["packet"]["change_risk"] = "MEDIUM"
    medium = plan(facts)
    assert medium["decision"] == "MERGE_READY"


def test_incomplete_dependency_does_not_admit() -> None:
    facts = load_fixture("01-admit-implementation.json")
    facts["packet"]["dependencies"] = [{"workstream": "p1c", "status": "ACTIVE"}]
    decision = plan(facts)
    assert decision["decision"] == "WAIT_EXTERNAL"
    assert decision["launches_worker"] is False


def test_admission_deny_does_not_admit() -> None:
    facts = load_fixture("01-admit-implementation.json")
    facts["admission"] = {"decision": "DENY", "deny_class": "WIP_LIMIT"}
    decision = plan(facts)
    assert decision["decision"] == "WAIT_EXTERNAL"
    assert decision["launches_worker"] is False


def test_host_budget_denial_yields_without_stopping_sessions() -> None:
    facts = load_fixture("01-admit-implementation.json")
    facts["admission"] = {"decision": "DENY", "deny_class": "HOST_BUDGET"}
    decision = plan(facts)
    assert decision["decision"] == "YIELD_RESOURCE"
    assert decision["stops_unrelated_sessions"] is False


def test_unknown_resource_does_not_invent_allow() -> None:
    facts = load_fixture("01-admit-implementation.json")
    del facts["resource"]
    decision = plan(facts)
    assert decision["decision"] == "WAIT_EXTERNAL"
    assert decision["launches_worker"] is False


def test_org_rollout_gate_blocks_merge() -> None:
    facts = load_fixture("08-merge-ready.json")
    facts["gates"] = {"org_rollout": "PENDING"}
    decision = plan(facts)
    assert decision["decision"] == "WAIT_EXTERNAL"


def test_transient_retry_is_bounded() -> None:
    facts = load_fixture("01-admit-implementation.json")
    facts["failure"] = {"class": "TRANSIENT", "identical_semantic_count": 0}
    facts["wait"] = {"external": False, "retry_count": 1, "retry_budget": 3}
    waiting = plan(facts)
    assert waiting["decision"] == "WAIT_EXTERNAL"
    facts["wait"]["retry_count"] = 3
    exhausted = plan(facts)
    assert exhausted["decision"] == "BLOCK_HUMAN"


def test_failure_class_ambiguous_reconciles() -> None:
    facts = load_fixture("01-admit-implementation.json")
    facts["failure"] = {"class": "AMBIGUOUS_MUTATION", "identical_semantic_count": 0}
    decision = plan(facts)
    assert decision["decision"] == "RECONCILE_AMBIGUOUS"


def test_execution_key_fails_closed() -> None:
    facts = load_fixture("01-admit-implementation.json")
    facts["command"] = "rm -rf /"
    try:
        plan(facts)
    except Exception as exc:
        assert "forbidden execution key" in str(exc)
    else:
        raise AssertionError("execution key was accepted")


def test_cli_plan_roundtrip() -> None:
    payload = load_fixture("09-yield-resource.json")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "facts.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        completed = subprocess.run(
            ["python3", str(TOOL), "plan", "--facts", str(path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr
    fields = {}
    for line in completed.stdout.splitlines():
        key, value = line.split("=", 1)
        fields[key] = value
    assert fields["DECISION"] == "YIELD_RESOURCE"
    assert fields["STOPS_UNRELATED_SESSIONS"] == "NO"
    assert fields["LAUNCHES_WORKER"] == "NO"
    assert fields["SPAWNS_PROCESS"] == "NO"
    assert fields["MUTATES_GITHUB"] == "NO"
    assert fields["SENDS_NOTIFICATION"] == "NO"


def test_cli_rejects_malformed_facts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "facts.json"
        path.write_text("{", encoding="utf-8")
        completed = subprocess.run(
            ["python3", str(TOOL), "plan", "--facts", str(path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert completed.returncode == 3
    assert "DECISION=REJECT_FACTS" in completed.stdout
    assert "LAUNCHES_WORKER=NO" in completed.stdout


def test_source_has_no_side_effects() -> None:
    text = TOOL.read_text(encoding="utf-8")
    for token in (
        "os.kill",
        "persist stop",
        "SIGKILL",
        "import subprocess",
        "subprocess.",
        "os.system",
        "shell=True",
        "urllib",
        "socket",
        "requests",
    ):
        assert token not in text, token


def main() -> int:
    test_fixtures()
    test_p2_coord_001_liveness_without_progress_does_not_resume()
    test_identical_wait_suppress_reason()
    test_resume_is_worker_microstep()
    test_non_active_never_launch_even_with_worker()
    test_priority_does_not_change_decision()
    test_stale_publication_revision_does_not_authorize()
    test_stale_ci_head_does_not_merge()
    test_high_risk_merge_requires_audit()
    test_incomplete_dependency_does_not_admit()
    test_admission_deny_does_not_admit()
    test_host_budget_denial_yields_without_stopping_sessions()
    test_unknown_resource_does_not_invent_allow()
    test_org_rollout_gate_blocks_merge()
    test_transient_retry_is_bounded()
    test_failure_class_ambiguous_reconciles()
    test_execution_key_fails_closed()
    test_cli_plan_roundtrip()
    test_cli_rejects_malformed_facts()
    test_source_has_no_side_effects()
    print("COORDINATOR_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
