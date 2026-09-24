#!/usr/bin/env python3
"""Deterministic regressions for the pure coordinator watch evaluator."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from coordinator_watch import evaluate  # noqa: E402

TOOL = ROOT / "tools" / "coordinator_watch.py"
FIXTURES = ROOT / "tools" / "fixtures" / "coordinator-watch"
SCHEMA = json.loads((ROOT / "schemas" / "coordinator-watch.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
EMPTY = json.loads((FIXTURES / "empty-state.json").read_text(encoding="utf-8"))


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def assert_schema(result: dict) -> None:
    errors = sorted(VALIDATOR.iter_errors(result), key=lambda item: list(item.path))
    assert not errors, errors[0].message


def assert_inert(result: dict) -> None:
    assert result["retries_action"] is False
    assert result["stops_unrelated_sessions"] is False
    assert result["mutates_existing_sessions"] is False
    assert result["spawns_process"] is False
    assert result["mutates_github"] is False
    assert result["sends_notification"] is False
    assert_schema(result)


def test_ci_pending_same_observation_rechecks_quietly() -> None:
    facts = load_fixture("01-ci-pending.json")
    first = evaluate(facts, EMPTY)
    assert_inert(first)
    assert first["result"] == "RECHECK_LATER"
    assert first["coordinator_decision"] == "WAIT_EXACT_HEAD_CI"
    assert first["notification_disposition"] == "SUPPRESS"
    assert first["wakes_coordinator"] is False
    assert first["resumes_worker"] is False
    later = load_fixture("01-ci-pending.json")
    later["watch"]["observed_at"] = "2026-09-24T16:10:00Z"
    second = evaluate(later, first["next_watch_state"])
    assert_inert(second)
    assert second["result"] == "RECHECK_LATER"
    assert second["notification_disposition"] == "SUPPRESS"
    assert second["notification_suppress_reason"] == "IDENTICAL_WAIT"
    assert second["wakes_coordinator"] is False
    assert second["next_eligible_check_at"] > first["next_eligible_check_at"]
    assert evaluate(later, first["next_watch_state"]) == second


def test_exact_head_ci_pass_wakes_coordinator() -> None:
    pending = evaluate(load_fixture("01-ci-pending.json"), EMPTY)
    woken = evaluate(load_fixture("02-ci-pass-exact.json"), pending["next_watch_state"])
    assert_inert(woken)
    assert woken["result"] == "WAKE_COORDINATOR"
    assert woken["wakes_coordinator"] is True
    assert woken["coordinator_decision"] == "AUDIT_REVIEW"
    assert woken["resumes_worker"] is False
    assert woken["notification_disposition"] == "SEND"


def test_stale_ci_pass_does_not_wake_merge() -> None:
    pending = evaluate(load_fixture("01-ci-pending.json"), EMPTY)
    stale = evaluate(load_fixture("03-ci-pass-stale-head.json"), pending["next_watch_state"])
    assert_inert(stale)
    assert stale["coordinator_decision"] == "WAIT_EXACT_HEAD_CI"
    assert stale["result"] == "RECHECK_LATER"
    assert stale["wakes_coordinator"] is False
    assert stale["coordinator_decision"] != "MERGE_READY"


def test_review_clear_on_exact_head_wakes_coordinator() -> None:
    opened = evaluate(load_fixture("04-review-open.json"), EMPTY)
    assert opened["coordinator_decision"] == "AUDIT_REVIEW"
    cleared = evaluate(load_fixture("04-review-clear.json"), opened["next_watch_state"])
    assert_inert(cleared)
    assert cleared["result"] == "WAKE_COORDINATOR"
    assert cleared["coordinator_decision"] == "MERGE_READY"
    assert cleared["wakes_coordinator"] is True
    assert cleared["resumes_worker"] is False


def test_liveness_without_progress_does_not_resume() -> None:
    result = evaluate(load_fixture("05-worker-no-progress.json"), EMPTY)
    assert_inert(result)
    assert result["result"] == "RECHECK_LATER"
    assert result["resumes_worker"] is False
    assert result["coordinator_decision"] == "WAIT_EXTERNAL"
    assert "positive progress" in result["reason"]


def test_positive_progress_resumes_admitted_worker() -> None:
    result = evaluate(load_fixture("06-worker-progress.json"), EMPTY)
    assert_inert(result)
    assert result["result"] == "RESUME_ADMITTED_WORKER"
    assert result["resumes_worker"] is True
    assert result["coordinator_decision"] == "RESUME_WORKER"
    assert result["wakes_coordinator"] is False
    assert result["stops_unrelated_sessions"] is False


def test_resource_and_wip_block_do_not_resume() -> None:
    for name in ("07-resource-block.json", "07-wip-block.json"):
        result = evaluate(load_fixture(name), EMPTY)
        assert_inert(result)
        assert result["result"] != "RESUME_ADMITTED_WORKER", name
        assert result["resumes_worker"] is False, name
        assert result["stops_unrelated_sessions"] is False, name
        assert result["mutates_existing_sessions"] is False, name


def test_stale_intent_revision_cannot_act() -> None:
    current = evaluate(load_fixture("06-worker-progress.json"), EMPTY)
    assert current["resumes_worker"] is True
    stale = evaluate(load_fixture("08-revision-advanced.json"), current["next_watch_state"])
    assert_inert(stale)
    assert stale["result"] == "CLOSE_WATCH"
    assert stale["closes_watch"] is True
    assert stale["resumes_worker"] is False
    assert stale["wakes_coordinator"] is False
    assert stale["retries_action"] is False
    assert stale["coordinator_decision"] == "STALE_WATCH"
    assert stale["next_watch_state"]["intent_revision"] == 3
    assert stale["next_watch_state"]["terminal_state"] == "CLOSED"


def test_ambiguous_mutation_blocks_without_retry() -> None:
    result = evaluate(load_fixture("09-ambiguous.json"), EMPTY)
    assert_inert(result)
    assert result["result"] == "BLOCK_RECONCILIATION"
    assert result["retries_action"] is False
    assert result["resumes_worker"] is False
    assert result["wakes_coordinator"] is False
    assert result["coordinator_decision"] == "RECONCILE_AMBIGUOUS"


def test_repeated_owner_notify_is_deduplicated() -> None:
    first = evaluate(load_fixture("10-blocked.json"), EMPTY)
    assert_inert(first)
    assert first["result"] == "NOTIFY_OWNER"
    assert first["notification_disposition"] == "SEND"
    assert first["resumes_worker"] is False
    second = evaluate(load_fixture("10-blocked.json"), first["next_watch_state"])
    assert_inert(second)
    assert second["result"] == "NO_CHANGE"
    assert second["notification_disposition"] == "SUPPRESS"
    assert second["notification_suppress_reason"] == "DEDUP"
    third = evaluate(load_fixture("10-blocked.json"), second["next_watch_state"])
    assert third["result"] == "NO_CHANGE"
    assert third["notification_disposition"] == "SUPPRESS"
    assert third["notification_suppress_reason"] == "DEDUP"


def test_complete_packet_closes_watch() -> None:
    result = evaluate(load_fixture("11-complete.json"), EMPTY)
    assert_inert(result)
    assert result["result"] == "CLOSE_WATCH"
    assert result["closes_watch"] is True
    assert result["coordinator_decision"] == "NOOP_COMPLETE"
    assert result["resumes_worker"] is False
    assert result["next_watch_state"]["terminal_state"] == "CLOSED"


def test_malformed_facts_and_execution_keys_fail_closed() -> None:
    facts = load_fixture("01-ci-pending.json")
    facts["command"] = "true"
    try:
        evaluate(facts, EMPTY)
    except Exception as exc:
        assert "forbidden execution key" in str(exc)
    else:
        raise AssertionError("execution key was accepted")
    unknown = load_fixture("01-ci-pending.json")
    unknown["notes"] = "not a fact"
    try:
        evaluate(unknown, EMPTY)
    except Exception as exc:
        assert "unknown key" in str(exc)
    else:
        raise AssertionError("unknown key was accepted")
    bad_class = load_fixture("01-ci-pending.json")
    bad_class["watch"]["watch_class"] = "production_mutation"
    try:
        evaluate(bad_class, EMPTY)
    except Exception as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("unknown watch class was accepted")
    with tempfile.TemporaryDirectory() as tmp:
        facts_path = Path(tmp) / "facts.json"
        state_path = Path(tmp) / "state.json"
        facts_path.write_text("{", encoding="utf-8")
        state_path.write_text("{}", encoding="utf-8")
        completed = subprocess.run(
            ["python3", str(TOOL), "evaluate", "--facts", str(facts_path), "--watch-state", str(state_path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert completed.returncode == 3
    assert "RESULT=REJECT_FACTS" in completed.stdout
    assert "RESUMES_WORKER=NO" in completed.stdout
    assert "SENDS_NOTIFICATION=NO" in completed.stdout


def test_source_has_no_side_effects() -> None:
    text = TOOL.read_text(encoding="utf-8")
    for token in (
        "import subprocess",
        "subprocess.",
        "os.system",
        "os.kill",
        "shell=True",
        "urllib",
        "socket",
        "requests",
        "http.client",
        "smtplib",
        "SIGKILL",
        "persist stop",
    ):
        assert token not in text, token


def test_existing_coordinator_regressions_remain_green() -> None:
    for script in (
        "tools/test_coordinator.py",
        "tools/test_worker_adapter.py",
        "tools/test_work_admission.py",
        "tools/test_independent_verifier.py",
    ):
        completed = subprocess.run(
            ["python3", script],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert completed.returncode == 0, script
        assert "PASS" in completed.stdout, script


def test_cli_evaluate_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        facts_path = Path(tmp) / "facts.json"
        state_path = Path(tmp) / "state.json"
        facts_path.write_text(json.dumps(load_fixture("06-worker-progress.json")), encoding="utf-8")
        state_path.write_text("{}\n", encoding="utf-8")
        completed = subprocess.run(
            ["python3", str(TOOL), "evaluate", "--facts", str(facts_path), "--watch-state", str(state_path)],
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
    assert fields["RESULT"] == "RESUME_ADMITTED_WORKER"
    assert fields["RESUMES_WORKER"] == "YES"
    assert fields["STOPS_UNRELATED_SESSIONS"] == "NO"
    assert fields["SPAWNS_PROCESS"] == "NO"
    assert fields["MUTATES_GITHUB"] == "NO"
    assert fields["SENDS_NOTIFICATION"] == "NO"
    assert fields["RETRIES_ACTION"] == "NO"


def main() -> int:
    test_ci_pending_same_observation_rechecks_quietly()
    test_exact_head_ci_pass_wakes_coordinator()
    test_stale_ci_pass_does_not_wake_merge()
    test_review_clear_on_exact_head_wakes_coordinator()
    test_liveness_without_progress_does_not_resume()
    test_positive_progress_resumes_admitted_worker()
    test_resource_and_wip_block_do_not_resume()
    test_stale_intent_revision_cannot_act()
    test_ambiguous_mutation_blocks_without_retry()
    test_repeated_owner_notify_is_deduplicated()
    test_complete_packet_closes_watch()
    test_malformed_facts_and_execution_keys_fail_closed()
    test_source_has_no_side_effects()
    test_cli_evaluate_roundtrip()
    test_existing_coordinator_regressions_remain_green()
    print("COORDINATOR_WATCH_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
