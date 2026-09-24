#!/usr/bin/env python3
"""Deterministic regressions for the coordinator watch run-once host."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from coordinator_watch import evaluate  # noqa: E402
from coordinator_watch_host import (  # noqa: E402
    HostRequestError,
    external_effect,
    owner_notice_effect,
    run_once,
)
import skills_contract  # noqa: E402
import skills_contract_fixtures as fixtures  # noqa: E402

FIXTURES = ROOT / "tools" / "fixtures" / "coordinator-watch"
SCHEMA = json.loads((ROOT / "schemas" / "coordinator-watch-host.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
SHA = "a" * 40
OTHER = "b" * 40
BRANCH = "feat/watch-host"
TARGET = "65"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def assert_schema(result: dict) -> None:
    errors = sorted(VALIDATOR.iter_errors(result), key=lambda item: list(item.path))
    assert not errors, errors[0].message


def assert_inert(result: dict) -> None:
    assert result["stops_unrelated_sessions"] is False
    assert result["mutates_existing_sessions"] is False
    assert result["spawns_process"] is False
    assert result["mutates_github"] is False
    assert result["executes_command"] is False
    assert result["busy_loop"] is False
    assert result["sleeps"] is False
    assert result["cadence"] == "EXTERNAL"
    assert result["actions_delivered"] == result["wakes"] + result["resumes"] + result["notifications"]
    assert result["actions_delivered"] in (0, 1)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def workspace(base: Path, facts: dict, authoritative: dict | None = None) -> dict:
    collected = base / "collected.json"
    refetch = base / "authoritative.json"
    write_json(collected, facts)
    write_json(refetch, authoritative or facts)
    return {
        "schema_version": 1,
        "target_repo": "datarelay-labs/engineering-system",
        "workstream": "p2-coordinator",
        "watch_class": facts["watch"]["watch_class"],
        "branch": BRANCH,
        "effect_target_id": TARGET,
        "lock_path": str(base / "host.lock"),
        "watch_state_path": str(base / "watch-state.json"),
        "ledger_path": str(base / "ledger.json"),
        "collected_facts_path": str(collected),
        "authoritative_facts_path": str(refetch),
        "work_budget": 1,
    }


class SignedDispatch:
    def __init__(self, base: Path, effect: dict, dispatch_id: str):
        self.base = base
        self.effect = effect
        self.dispatch_id = dispatch_id
        self.previous_anchor = skills_contract._TEST_TRUST_ANCHOR_PATH
        self.previous_replay = skills_contract._TEST_REPLAY_BOUNDARY_AVAILABLE
        self.previous_store = skills_contract._TEST_REPLAY_STORE

    def __enter__(self) -> dict[str, str]:
        priv, pub = fixtures.generate_keypair(self.base / "keys")
        _, _, digest = skills_contract.load_effective_state(ROOT)
        binding = self.base / "binding.json"
        dispatch = self.base / "dispatch.json"
        fixtures.write_binding_assertion(
            binding,
            private_key=priv,
            public_key=pub,
            profile="external_write",
            policy_digest=digest,
            authority_permission="admin",
            approved_classes=["external_write"],
        )
        fixtures.write_dispatch_assertion(
            dispatch,
            private_key=priv,
            public_key=pub,
            tool_id="network.post",
            classes=skills_contract.DEFAULT_TOOL_REGISTRY["network.post"],
            policy_digest=digest,
            request_payload=self.effect,
            dispatch_id=self.dispatch_id,
            expires_at_unix=int(time.time()) + 3600,
        )
        skills_contract._TEST_TRUST_ANCHOR_PATH = pub
        skills_contract._TEST_REPLAY_BOUNDARY_AVAILABLE = True
        skills_contract._TEST_REPLAY_STORE = set()
        return {"binding_assertion": str(binding), "dispatch_assertion": str(dispatch)}

    def __exit__(self, *_args: object) -> None:
        skills_contract._TEST_TRUST_ANCHOR_PATH = self.previous_anchor
        skills_contract._TEST_REPLAY_BOUNDARY_AVAILABLE = self.previous_replay
        skills_contract._TEST_REPLAY_STORE = self.previous_store


def test_unchanged_wait_takes_no_action() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        request = workspace(Path(tmp), load_fixture("01-ci-pending.json"))
        result = run_once(request)
        assert_schema(result)
        assert_inert(result)
        assert result["result"] == "NO_ACTION"
        assert result["watch_result"] == "RECHECK_LATER"
        assert result["actions_delivered"] == 0
        assert result["lock"] == "ACQUIRED"
        assert result["next_eligible_check_at"]
        assert not Path(request["ledger_path"]).exists()


def test_lock_already_owned_does_not_evaluate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        request = workspace(base, load_fixture("02-ci-pass-exact.json"))
        holder_code = (
            "import fcntl, os, sys, time\n"
            "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o644)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "sys.stdout.write('held\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", holder_code, request["lock_path"]],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "held"
            result = run_once(request)
        finally:
            holder.kill()
            holder.wait(timeout=5)
        assert_schema(result)
        assert_inert(result)
        assert result["result"] == "LOCK_HELD"
        assert result["lock"] == "HELD"
        assert result["actions_delivered"] == 0
        assert result["watch_result"] == "NONE"
        assert not Path(request["watch_state_path"]).exists()
        assert not Path(request["ledger_path"]).exists()


def test_exact_head_ci_wakes_once_and_replay_dedups() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    preview = evaluate(facts, {})
    effect = external_effect(
        preview,
        repository="datarelay-labs/engineering-system",
        workstream="p2-coordinator",
        intent_revision=3,
        subject_head=SHA,
        status="ACTIVE",
        branch=BRANCH,
        effect_target_id=TARGET,
    )
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with SignedDispatch(base, effect, "watch-host-wake-1") as verification:
            request = workspace(base, facts)
            request["verification"] = verification
            first = run_once(request)
            assert_schema(first)
            assert_inert(first)
            assert first["result"] == "DELIVERED"
            assert first["wakes"] == 1
            assert first["resumes"] == 0
            assert first["notifications"] == 0
            assert first["mutates_github"] is False
            second = run_once(request)
            assert_schema(second)
            assert second["wakes"] == 0
            assert second["actions_delivered"] == 0
            assert second["result"] in {"DEDUP", "NO_ACTION"}
            ledger = json.loads(Path(request["ledger_path"]).read_text(encoding="utf-8"))
            assert ledger["applied"][0]["outcome"] == "APPLIED"
            assert len(ledger["applied"]) == 1


def test_restart_from_ledger_does_not_duplicate() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    preview = evaluate(facts, {})
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        request = workspace(base, facts)
        write_json(
            Path(request["ledger_path"]),
            {
                "schema_version": 1,
                "applied": [
                    {
                        "key": preview["notification_key"],
                        "outcome": "APPLIED",
                        "result": "WAKE_COORDINATOR",
                    }
                ],
            },
        )
        result = run_once(request)
        assert_schema(result)
        assert result["result"] == "DEDUP"
        assert result["wakes"] == 0
        assert result["actions_delivered"] == 0
        ledger = json.loads(Path(request["ledger_path"]).read_text(encoding="utf-8"))
        assert ledger["applied"] == [
            {
                "key": preview["notification_key"],
                "outcome": "APPLIED",
                "result": "WAKE_COORDINATOR",
            }
        ]


def test_progress_resumes_once_through_trusted_boundary() -> None:
    facts = load_fixture("06-worker-progress.json")
    preview = evaluate(facts, {})
    assert preview["result"] == "RESUME_ADMITTED_WORKER"
    effect = external_effect(
        preview,
        repository="datarelay-labs/engineering-system",
        workstream="p2-coordinator",
        intent_revision=3,
        subject_head=SHA,
        status="ACTIVE",
        branch=BRANCH,
        effect_target_id=TARGET,
    )
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with SignedDispatch(base, effect, "watch-host-resume-1") as verification:
            request = workspace(base, facts)
            request["verification"] = verification
            result = run_once(request)
            assert_schema(result)
            assert_inert(result)
            assert result["result"] == "DELIVERED"
            assert result["resumes"] == 1
            assert result["wakes"] == 0
            assert result["mutates_github"] is False
            assert result["stops_unrelated_sessions"] is False


def test_liveness_without_progress_does_not_resume() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        request = workspace(Path(tmp), load_fixture("05-worker-no-progress.json"))
        result = run_once(request)
        assert_schema(result)
        assert result["resumes"] == 0
        assert result["actions_delivered"] == 0
        assert result["result"] == "NO_ACTION"


def test_revision_change_before_action_delivers_nothing() -> None:
    collected = load_fixture("02-ci-pass-exact.json")
    authoritative = copy.deepcopy(collected)
    authoritative["packet"]["intent_revision"] = 4
    with tempfile.TemporaryDirectory() as tmp:
        request = workspace(Path(tmp), collected, authoritative)
        result = run_once(request)
        assert_schema(result)
        assert result["result"] == "STALE_RECONCILE"
        assert result["wakes"] == 0
        assert result["actions_delivered"] == 0
        assert not Path(request["ledger_path"]).exists()


def test_subject_change_before_action_delivers_nothing() -> None:
    collected = load_fixture("02-ci-pass-exact.json")
    authoritative = copy.deepcopy(collected)
    authoritative["git"]["head"] = OTHER
    authoritative["pr"]["head"] = OTHER
    authoritative["ci"]["subject_head"] = OTHER
    with tempfile.TemporaryDirectory() as tmp:
        request = workspace(Path(tmp), collected, authoritative)
        result = run_once(request)
        assert_schema(result)
        assert result["result"] == "STALE_RECONCILE"
        assert result["wakes"] == 0
        assert result["actions_delivered"] == 0


def test_resource_block_before_resume_leaves_sessions_untouched() -> None:
    collected = load_fixture("06-worker-progress.json")
    authoritative = copy.deepcopy(collected)
    authoritative["resource"]["result"] = "BLOCK"
    authoritative["admission"] = {"decision": "DENY", "deny_class": "WIP_LIMIT"}
    with tempfile.TemporaryDirectory() as tmp:
        request = workspace(Path(tmp), collected, authoritative)
        result = run_once(request)
        assert_schema(result)
        assert_inert(result)
        assert result["result"] == "RESOURCE_BLOCKED"
        assert result["resumes"] == 0
        assert result["actions_delivered"] == 0
        assert not Path(request["ledger_path"]).exists()


def test_ambiguous_prior_action_is_not_retried() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    preview = evaluate(facts, {})
    with tempfile.TemporaryDirectory() as tmp:
        request = workspace(Path(tmp), facts)
        write_json(
            Path(request["ledger_path"]),
            {
                "schema_version": 1,
                "applied": [
                    {
                        "key": preview["notification_key"],
                        "outcome": "AMBIGUOUS",
                        "result": "WAKE_COORDINATOR",
                    }
                ],
            },
        )
        result = run_once(request)
        assert_schema(result)
        assert result["result"] == "RECONCILE_AMBIGUOUS"
        assert result["wakes"] == 0
        assert result["actions_delivered"] == 0
        ledger = json.loads(Path(request["ledger_path"]).read_text(encoding="utf-8"))
        assert ledger["applied"][0]["outcome"] == "AMBIGUOUS"
        assert len(ledger["applied"]) == 1


def test_missing_trusted_dispatch_denies_external_effect() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        request = workspace(Path(tmp), load_fixture("02-ci-pass-exact.json"))
        result = run_once(request)
        assert_schema(result)
        assert result["result"] == "AUTHORITY_DENIED"
        assert result["wakes"] == 0
        assert result["actions_delivered"] == 0
        assert result["mutates_github"] is False
        assert not Path(request["ledger_path"]).exists()


def test_owner_notice_is_verified_and_deduplicated() -> None:
    facts = load_fixture("10-blocked.json")
    preview = evaluate(facts, {})
    assert preview["result"] == "NOTIFY_OWNER"
    effect = owner_notice_effect(preview)
    assert effect["level"] == "INFO"
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with SignedDispatch(base, effect, "watch-host-notice-1") as verification:
            request = workspace(base, facts)
            request["verification"] = verification
            first = run_once(request)
            assert_schema(first)
            assert_inert(first)
            assert first["result"] == "DELIVERED"
            assert first["notifications"] == 1
            assert first["notification_level"] == "INFO"
            assert first["notification_delivery"] == "VERIFIED"
            assert first["notification_level"] != "COMPLETE"
            second = run_once(request)
            assert second["notifications"] == 0
            assert second["actions_delivered"] == 0
            assert second["result"] in {"DEDUP", "NO_ACTION"}


def test_persisted_state_is_bounded_metadata() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    preview = evaluate(facts, {})
    effect = external_effect(
        preview,
        repository="datarelay-labs/engineering-system",
        workstream="p2-coordinator",
        intent_revision=3,
        subject_head=SHA,
        status="ACTIVE",
        branch=BRANCH,
        effect_target_id=TARGET,
    )
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with SignedDispatch(base, effect, "watch-host-meta-1") as verification:
            request = workspace(base, facts)
            request["verification"] = verification
            result = run_once(request)
            assert result["result"] == "DELIVERED"
            ledger_text = Path(request["ledger_path"]).read_text(encoding="utf-8")
            state_text = Path(request["watch_state_path"]).read_text(encoding="utf-8")
            assert len(ledger_text) < 4000
            assert len(state_text) < 4000
            for forbidden in ("secret", "password", "prompt", "chat", "BEGIN ", "private_key"):
                assert forbidden not in ledger_text
                assert forbidden not in state_text
            ledger = json.loads(ledger_text)
            assert set(ledger) == {"applied", "schema_version"}
            assert set(ledger["applied"][0]) == {"key", "outcome", "result"}


def test_execution_inputs_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        request = workspace(base, load_fixture("01-ci-pending.json"))
        request["command"] = "echo unsafe"
        try:
            run_once(request)
        except HostRequestError as exc:
            assert exc.deny_class == "EXECUTION_FORBIDDEN"
        else:
            raise AssertionError("command field was accepted")
        facts = load_fixture("01-ci-pending.json")
        facts["endpoint"] = "https://example.invalid/hook"
        write_json(Path(request["collected_facts_path"]), facts)
        request.pop("command")
        try:
            run_once(request)
        except HostRequestError as exc:
            assert exc.deny_class == "EXECUTION_FORBIDDEN"
        else:
            raise AssertionError("endpoint field was accepted")
        request["work_budget"] = 4
        request["collected_facts_path"] = str(FIXTURES / "01-ci-pending.json")
        try:
            run_once(request)
        except HostRequestError as exc:
            assert "work_budget" in exc.reason
        else:
            raise AssertionError("unbounded work budget was accepted")


def test_source_has_a_finite_run() -> None:
    text = (ROOT / "tools" / "coordinator_watch_host.py").read_text(encoding="utf-8")
    for token in (
        "import subprocess",
        "subprocess.",
        "os.system",
        "os.kill",
        "shell=True",
        "urllib",
        "socket",
        "time.sleep",
        "while ",
        "SIGKILL",
    ):
        assert token not in text, token


def test_cli_quiet_wait() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        request = workspace(base, load_fixture("01-ci-pending.json"))
        path = base / "request.json"
        write_json(path, request)
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "coordinator_watch_host.py"), "run-once", "--request", str(path)],
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
    assert fields["RESULT"] == "NO_ACTION"
    assert fields["ACTIONS_DELIVERED"] == "0"
    assert fields["WAKES"] == "0"
    assert fields["RESUMES"] == "0"
    assert fields["NOTIFICATIONS"] == "0"
    assert fields["STOPS_UNRELATED_SESSIONS"] == "NO"
    assert fields["MUTATES_GITHUB"] == "NO"
    assert fields["EXECUTES_COMMAND"] == "NO"
    assert fields["BUSY_LOOP"] == "NO"
    assert fields["SLEEPS"] == "NO"
    assert fields["CADENCE"] == "EXTERNAL"


def main_tests() -> int:
    test_unchanged_wait_takes_no_action()
    test_lock_already_owned_does_not_evaluate()
    test_exact_head_ci_wakes_once_and_replay_dedups()
    test_restart_from_ledger_does_not_duplicate()
    test_progress_resumes_once_through_trusted_boundary()
    test_liveness_without_progress_does_not_resume()
    test_revision_change_before_action_delivers_nothing()
    test_subject_change_before_action_delivers_nothing()
    test_resource_block_before_resume_leaves_sessions_untouched()
    test_ambiguous_prior_action_is_not_retried()
    test_missing_trusted_dispatch_denies_external_effect()
    test_owner_notice_is_verified_and_deduplicated()
    test_persisted_state_is_bounded_metadata()
    test_execution_inputs_fail_closed()
    test_source_has_a_finite_run()
    test_cli_quiet_wait()
    print("COORDINATOR_WATCH_HOST_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_tests())
