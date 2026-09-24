#!/usr/bin/env python3
"""Deterministic regressions for the trusted worker external-write gate."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from worker_adapter import concrete_effect, evaluate, request_digest  # noqa: E402

TOOL = ROOT / "tools" / "worker_adapter.py"
SCHEMA = json.loads((ROOT / "schemas" / "worker-adapter-result.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
SHA = "e" * 40
OTHER = "f" * 40
REV3_BODY = "INTENT_REVISION=3 rewrite of Current State that must not land"


def bound(**overrides: object) -> dict:
    value = {
        "target_repo": "datarelay-labs/engineering-system",
        "workstream": "p2-coordinator",
        "intent_revision": 6,
        "subject_head": SHA,
        "requested_action": "update_work_packet",
        "branch": "feat/engineering-system-1.7.0-p2-worker-adapter",
        "expected_status": "ACTIVE",
    }
    value.update(overrides)
    return value


def authoritative(**overrides: object) -> dict:
    value = {
        "target_repo": "datarelay-labs/engineering-system",
        "workstream": "p2-coordinator",
        "intent_revision": 6,
        "status": "ACTIVE",
        "subject_head": SHA,
        "branch": "feat/engineering-system-1.7.0-p2-worker-adapter",
    }
    value.update(overrides)
    return value


def proposed(content: str = "packet body") -> dict:
    return {"target": {"kind": "issue", "id": "42"}, "content": content}


def request(bound_request: dict | None = None, **overrides: object) -> dict:
    chosen = bound_request or bound()
    payload = {
        "bound_request": chosen,
        "authoritative": authoritative(),
        "author_permission": "admin",
        "author_association": "OWNER",
        "trusted_dispatch": {
            "result": "PASS",
            "request_sha256": "ignored-caller-supplied-digest",
            "dispatch_id": "caller-minted",
            "action_class": "external_write",
        },
        "proposed_mutation": proposed(),
        "resource": {"result": "PASS"},
        "admission": {"decision": "ALLOW"},
        "mutation": {"outcome": "NOT_SENT"},
        "failure": {"class": "NONE"},
    }
    payload.update(overrides)
    return payload


def assert_schema(decision: dict) -> None:
    errors = sorted(VALIDATOR.iter_errors(decision), key=lambda item: list(item.path))
    assert not errors, errors[0].message


def test_fabricated_permission_and_dispatch_cannot_apply() -> None:
    decision = evaluate(request())
    assert_schema(decision)
    assert decision["result"] == "AUTHORITY_DENIED"
    assert decision["authorizes_write"] is False
    assert decision["writes"] == 0
    assert decision["mints_authority"] is False
    assert evaluate(request()) == decision


def test_signed_dispatch_binds_exact_mutation_content() -> None:
    import time

    import skills_contract
    import skills_contract_fixtures as fixtures

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        priv, pub = fixtures.generate_keypair(base / "keys")
        _, _, digest = skills_contract.load_effective_state(ROOT)
        safe = request(proposed_mutation=proposed("safe body"))
        effect = concrete_effect(safe["bound_request"], safe["proposed_mutation"])
        binding = base / "binding.json"
        dispatch = base / "dispatch.json"
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
            request_payload=effect,
            dispatch_id="p2-worker-exact-1",
            expires_at_unix=int(time.time()) + 3600,
        )
        safe["verification"] = {
            "binding_assertion": str(binding),
            "dispatch_assertion": str(dispatch),
        }
        previous_anchor = skills_contract._TEST_TRUST_ANCHOR_PATH
        previous_replay = skills_contract._TEST_REPLAY_BOUNDARY_AVAILABLE
        previous_store = skills_contract._TEST_REPLAY_STORE
        skills_contract._TEST_TRUST_ANCHOR_PATH = pub
        skills_contract._TEST_REPLAY_BOUNDARY_AVAILABLE = True
        skills_contract._TEST_REPLAY_STORE = set()
        try:
            allowed = evaluate(safe)
            assert_schema(allowed)
            assert allowed["result"] == "APPLIED"
            assert allowed["writes"] == 1
            assert allowed["request_digest"] == request_digest(safe["bound_request"], safe["proposed_mutation"])
            changed = request(proposed_mutation=proposed("attacker body"))
            changed["verification"] = safe["verification"]
            denied = evaluate(changed)
            assert denied["result"] == "AUTHORITY_DENIED"
            assert denied["writes"] == 0
            assert "concrete mutation" in denied["reason"]
            replay = evaluate(safe)
            assert replay["result"] == "NO_CHANGE"
            assert replay["writes"] == 0
        finally:
            skills_contract._TEST_TRUST_ANCHOR_PATH = previous_anchor
            skills_contract._TEST_REPLAY_BOUNDARY_AVAILABLE = previous_replay
            skills_contract._TEST_REPLAY_STORE = previous_store


def test_revision_change_before_write_is_stale() -> None:
    decision = evaluate(request(authoritative=authoritative(intent_revision=7)))
    assert decision["result"] == "STALE_WORKER"
    assert decision["writes"] == 0
    assert decision["authorizes_write"] is False


def test_observed_stale_issue_write_race_does_not_touch_newer_state() -> None:
    facts = request(
        bound_request=bound(intent_revision=3),
        authoritative=authoritative(intent_revision=4),
        proposed_mutation={"target": {"kind": "issue", "id": "42"}, "content": REV3_BODY},
    )
    decision = evaluate(facts)
    assert decision["result"] == "STALE_WORKER"
    assert decision["writes"] == 0
    assert decision["issue_mutated"] is False
    assert REV3_BODY not in decision["reason"]
    assert "intent_revision" in decision["reason"]


def test_ambiguous_mutation_reconciles_without_duplicate() -> None:
    decision = evaluate(request(mutation={"outcome": "TIMEOUT"}))
    assert decision["result"] == "RECONCILE_AMBIGUOUS"
    assert decision["writes"] == 0
    again = evaluate(request(mutation={"outcome": "UNKNOWN"}))
    assert again["result"] == "RECONCILE_AMBIGUOUS"
    assert again["authorizes_write"] is False


def test_same_request_replay_is_idempotent() -> None:
    facts = request()
    digest = request_digest(facts["bound_request"], facts["proposed_mutation"])
    replay = evaluate(request(prior_delivery={"applied_digest": digest}))
    assert replay["result"] == "NO_CHANGE"
    assert replay["writes"] == 0
    observed = evaluate(request(mutation={"outcome": "APPLIED"}))
    assert observed["result"] == "NO_CHANGE"


def test_missing_or_untrusted_dispatch_is_denied() -> None:
    missing = request()
    assert evaluate(missing)["result"] == "AUTHORITY_DENIED"
    weak_binding = request(author_permission="read")
    denied = evaluate(weak_binding)
    assert denied["result"] == "AUTHORITY_DENIED"
    assert denied["authorizes_write"] is False
    assert denied["mints_authority"] is False


def test_resource_block_does_not_stop_unrelated_sessions() -> None:
    decision = evaluate(request(resource={"result": "BLOCK"}))
    assert decision["result"] == "RESOURCE_BLOCKED"
    assert decision["writes"] == 0
    assert decision["stops_unrelated_sessions"] is False
    host = evaluate(request(admission={"decision": "DENY", "deny_class": "HOST_BUDGET"}))
    assert host["result"] == "RESOURCE_BLOCKED"
    assert host["stops_unrelated_sessions"] is False


def test_caller_supplied_shell_endpoint_or_mint_is_rejected() -> None:
    for key in ("shell", "endpoint", "signing_key"):
        facts = request()
        facts[key] = "untrusted"
        decision = evaluate(facts)
        assert decision["result"] == "AUTHORITY_DENIED", key
        assert decision["writes"] == 0
        assert decision["spawns_process"] is False
    arbitrary = request(bound_request=bound(requested_action="run_shell"))
    assert evaluate(arbitrary)["result"] == "AUTHORITY_DENIED"


def test_subject_head_change_does_not_finalize() -> None:
    decision = evaluate(request(authoritative=authoritative(subject_head=OTHER)))
    assert decision["result"] == "STALE_WORKER"
    assert decision["authorizes_write"] is False
    assert "subject_head" in decision["reason"]


def test_cli_evaluate_roundtrip() -> None:
    payload = request()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "request.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        completed = subprocess.run(
            ["python3", str(TOOL), "evaluate", "--request-json", str(path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr
    fields = dict(line.split("=", 1) for line in completed.stdout.splitlines())
    assert fields["RESULT"] == "AUTHORITY_DENIED"
    assert fields["WRITES"] == "0"
    assert fields["ISSUE_MUTATED"] == "NO"
    assert fields["STOPS_UNRELATED_SESSIONS"] == "NO"
    assert fields["MINTS_AUTHORITY"] == "NO"


def test_source_has_no_side_effects() -> None:
    text = TOOL.read_text(encoding="utf-8")
    for token in ("import subprocess", "subprocess.", "os.system", "os.kill", "shell=True", "urllib", "socket"):
        assert token not in text, token


def main() -> int:
    test_fabricated_permission_and_dispatch_cannot_apply()
    test_signed_dispatch_binds_exact_mutation_content()
    test_revision_change_before_write_is_stale()
    test_observed_stale_issue_write_race_does_not_touch_newer_state()
    test_ambiguous_mutation_reconciles_without_duplicate()
    test_same_request_replay_is_idempotent()
    test_missing_or_untrusted_dispatch_is_denied()
    test_resource_block_does_not_stop_unrelated_sessions()
    test_caller_supplied_shell_endpoint_or_mint_is_rejected()
    test_subject_head_change_does_not_finalize()
    test_cli_evaluate_roundtrip()
    test_source_has_no_side_effects()
    print("WORKER_ADAPTER_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
