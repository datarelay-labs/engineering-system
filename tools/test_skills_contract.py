#!/usr/bin/env python3
"""Deterministic regressions for skills/hooks permission contract."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "skills-contract.py"
FIXTURES = ROOT / "tools" / "skills_contract_fixtures.py"

sys.path.insert(0, str(ROOT / "tools"))
_SPEC = importlib.util.spec_from_file_location("skills_contract", TOOL)
assert _SPEC and _SPEC.loader
contract = importlib.util.module_from_spec(_SPEC)
sys.modules["skills_contract"] = contract
_SPEC.loader.exec_module(contract)

_FSPEC = importlib.util.spec_from_file_location("skills_contract_fixtures", FIXTURES)
assert _FSPEC and _FSPEC.loader
fixtures = importlib.util.module_from_spec(_FSPEC)
_FSPEC.loader.exec_module(fixtures)


@contextmanager
def trust_anchor(pub: Path, *, replay: bool | None = None):
    previous_anchor = contract._TEST_TRUST_ANCHOR_PATH
    previous_replay = contract._TEST_REPLAY_BOUNDARY_AVAILABLE
    previous_store = contract._TEST_REPLAY_STORE
    previous_reservations = contract._TEST_DISPATCH_RESERVATIONS
    contract._TEST_TRUST_ANCHOR_PATH = pub
    if replay is True:
        contract._TEST_REPLAY_BOUNDARY_AVAILABLE = True
        contract._TEST_REPLAY_STORE = set()
        contract._TEST_DISPATCH_RESERVATIONS = {}
    elif replay is False:
        contract._TEST_REPLAY_BOUNDARY_AVAILABLE = False
        contract._TEST_REPLAY_STORE = None
        contract._TEST_DISPATCH_RESERVATIONS = None
    try:
        yield
    finally:
        contract._TEST_TRUST_ANCHOR_PATH = previous_anchor
        contract._TEST_REPLAY_BOUNDARY_AVAILABLE = previous_replay
        contract._TEST_REPLAY_STORE = previous_store
        contract._TEST_DISPATCH_RESERVATIONS = previous_reservations


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ)
    merged["PYTHONPATH"] = str(ROOT / "tools") + os.pathsep + merged.get("PYTHONPATH", "")
    if env is not None:
        merged = dict(env)
        merged["PYTHONPATH"] = str(ROOT / "tools") + os.pathsep + os.environ.get("PYTHONPATH", "")
        merged["PATH"] = os.environ.get("PATH", "")
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        cwd=str(ROOT),
        env=merged,
    )


def write_schema(root: Path) -> None:
    schema = root / "schemas"
    schema.mkdir(parents=True, exist_ok=True)
    (schema / "skills-contract.schema.json").write_text(
        (ROOT / "schemas" / "skills-contract.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def write_contract(root: Path, payload: dict) -> None:
    (root / ".engineering").mkdir(parents=True, exist_ok=True)
    (root / ".engineering" / "skills.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def test_production_cli_is_verification_only() -> None:
    help_top = run_cli("--help").stdout
    assert "{check,authorize}" in help_top.replace(" ", "")
    for banned in ("keygen", "bind", "dispatch", "host-authorize"):
        bad = run_cli(banned, "--help")
        assert bad.returncode != 0
        lower = bad.stdout.lower()
        assert "invalid choice" in lower or "unrecognized arguments" in lower or "error:" in lower
    auth_help = run_cli("authorize", "--help").stdout
    assert "--binding-assertion" in auth_help
    assert "--dispatch-assertion" in auth_help
    assert "--tool" not in auth_help
    assert "--binding-json" not in auth_help
    assert "--classes" not in auth_help
    assert "--trust-anchor" not in auth_help
    # Design guidance: no same-user keyed-MAC/bind mint in the production helper.
    source = TOOL.read_text(encoding="utf-8").lower()
    for banned_token in ("hmac.", "hmac.new", "binding_secret", 'add_parser("bind"', 'add_parser("keygen"', 'add_parser("dispatch"'):
        assert banned_token not in source
    assert "boundary_unavailable" in source


def test_authorize_without_host_trust_anchor_is_boundary_unavailable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        write_schema(root)
        # Even with caller env set, production CLI must ignore it.
        keys = Path(tmp) / "keys"
        _, pub = fixtures.generate_keypair(keys)
        env = dict(os.environ)
        env[contract.TRUST_ANCHOR_ENV] = str(pub)
        result = run_cli(
            "authorize",
            "--root",
            str(root),
            "--binding-assertion",
            str(root / "missing-binding.json"),
            "--dispatch-assertion",
            str(root / "missing-dispatch.json"),
            env=env,
        )
        assert result.returncode != 0
        assert "REASON=BOUNDARY_UNAVAILABLE" in result.stdout


def test_caller_env_trust_anchor_does_not_alter_authorization() -> None:
    """Round 5: caller env/repo paths must not select the production trust anchor."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        write_schema(root)
        priv, pub = fixtures.generate_keypair(base / "keys")
        # Repo-local decoy must not become the production anchor.
        decoy_dir = root / ".engineering"
        decoy_dir.mkdir(parents=True, exist_ok=True)
        decoy = decoy_dir / "skills-trust-anchor.pub"
        decoy.write_bytes(pub.read_bytes())
        os.chmod(decoy, 0o444)
        _, _, digest = contract.load_effective_state(root)
        request = {"op": "list", "path": "README.md"}
        binding = base / "binding.json"
        dispatch = base / "dispatch.json"
        fixtures.write_binding_assertion(
            binding,
            private_key=priv,
            public_key=pub,
            profile="repo_write",
            policy_digest=digest,
            authority_permission="write",
        )
        fixtures.write_dispatch_assertion(
            dispatch,
            private_key=priv,
            public_key=pub,
            tool_id="shell.local",
            classes=contract.DEFAULT_TOOL_REGISTRY["shell.local"],
            policy_digest=digest,
            request_payload=request,
        )
        env = dict(os.environ)
        env[contract.TRUST_ANCHOR_ENV] = str(pub)
        env["ENGINEERING_SKILLS_TRUST_ANCHOR"] = str(pub)
        env["SKILLS_TRUST_ANCHOR_PUBKEY"] = str(decoy)
        denied = run_cli(
            "authorize",
            "--root",
            str(root),
            "--binding-assertion",
            str(binding),
            "--dispatch-assertion",
            str(dispatch),
            "--request-json",
            json.dumps(request),
            env=env,
        )
        assert denied.returncode != 0
        assert "REASON=BOUNDARY_UNAVAILABLE" in denied.stdout
        # Source must not read caller environment for trust-anchor resolution.
        source = TOOL.read_text(encoding="utf-8")
        assert "os.environ" not in source
        assert "environ.get" not in source
        assert "HOST_TRUST_ANCHOR_PATH" in source


def test_missing_body_and_hook_kind_and_digest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        write_schema(root)
        write_contract(
            root,
            {
                "version": 1,
                "skills": [{"id": "x", "trigger": "t", "body": "skills/missing/SKILL.md"}],
            },
        )
        report = contract.check_contract(root)
        assert report["result"] == "FAIL"
        assert any(item["code"] == "PATH_MISSING" for item in report["findings"])

        skills = root / "skills" / "demo"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("# ok\n", encoding="utf-8")
        write_contract(
            root,
            {
                "version": 1,
                "skills": [{"id": "demo", "trigger": "t", "body": "skills/demo/SKILL.md"}],
                "hooks": {"pre_tool": [{"id": "gate", "kind": "command"}]},
            },
        )
        assert contract.check_contract(root)["result"] == "FAIL"
        write_contract(
            root,
            {
                "version": 1,
                "skills": [{"id": "demo", "trigger": "t", "body": "skills/demo/SKILL.md"}],
                "hooks": {"pre_tool": [{"id": "a", "kind": "metadata"}]},
            },
        )
        d1 = contract.check_contract(root)["policy_digest"]
        write_contract(
            root,
            {
                "version": 1,
                "skills": [{"id": "demo", "trigger": "t", "body": "skills/demo/SKILL.md"}],
                "hooks": {"pre_tool": [{"id": "b", "kind": "metadata"}]},
            },
        )
        d2 = contract.check_contract(root)["policy_digest"]
        assert d1 != d2


def test_authorize_with_fixture_assertions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        write_schema(root)
        priv, pub = fixtures.generate_keypair(base / "keys")
        _, _, digest = contract.load_effective_state(root)
        request = {"op": "echo", "argv": ["hello"]}
        binding = base / "binding.json"
        dispatch = base / "dispatch.json"
        fixtures.write_binding_assertion(
            binding,
            private_key=priv,
            public_key=pub,
            profile="repo_write",
            policy_digest=digest,
            authority_permission="write",
        )
        fixtures.write_dispatch_assertion(
            dispatch,
            private_key=priv,
            public_key=pub,
            tool_id="shell.local",
            classes=contract.DEFAULT_TOOL_REGISTRY["shell.local"],
            policy_digest=digest,
            request_payload=request,
        )
        with trust_anchor(pub):
            decision = contract.authorize(
                root,
                binding_assertion=binding,
                dispatch_assertion=dispatch,
                request_json=json.dumps(request),
            )
        assert decision.allowed, decision.reason


def test_caller_cannot_self_promote_with_own_keypair_against_pinned_anchor() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        write_schema(root)
        c_priv, c_pub = fixtures.generate_keypair(base / "coord")
        a_priv, a_pub = fixtures.generate_keypair(base / "atk")
        _, _, digest = contract.load_effective_state(root)
        request = {"target": "prod-db", "op": "write"}
        binding = base / "atk-binding.json"
        dispatch = base / "atk-dispatch.json"
        fixtures.write_binding_assertion(
            binding,
            private_key=a_priv,
            public_key=a_pub,
            profile="production_write",
            policy_digest=digest,
            authority_permission="admin",
            approved_classes=["production_write", "destructive", "external_write"],
        )
        fixtures.write_dispatch_assertion(
            dispatch,
            private_key=a_priv,
            public_key=a_pub,
            tool_id="production.write",
            classes=contract.DEFAULT_TOOL_REGISTRY["production.write"],
            policy_digest=digest,
            request_payload=request,
            dispatch_id="atk-1",
            expires_at_unix=int(time.time()) + 3600,
        )
        with trust_anchor(c_pub, replay=True):
            decision = contract.authorize(
                root,
                binding_assertion=binding,
                dispatch_assertion=dispatch,
                request_json=json.dumps(request),
            )
        assert not decision.allowed
        assert decision.reason in {"SIGNATURE_MISMATCH", "TRUST_ANCHOR_MISMATCH"}
        del c_priv


def test_request_json_cannot_under_classify() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        write_schema(root)
        priv, pub = fixtures.generate_keypair(base / "keys")
        _, _, digest = contract.load_effective_state(root)
        request_a = {"op": "upload", "url": "https://example.invalid"}
        binding = base / "binding.json"
        dispatch = base / "dispatch.json"
        fixtures.write_binding_assertion(
            binding,
            private_key=priv,
            public_key=pub,
            profile="repo_write",
            policy_digest=digest,
            authority_permission="write",
        )
        fixtures.write_dispatch_assertion(
            dispatch,
            private_key=priv,
            public_key=pub,
            tool_id="shell.external_write",
            classes=contract.DEFAULT_TOOL_REGISTRY["shell.external_write"],
            policy_digest=digest,
            request_payload=request_a,
            dispatch_id="ext-1",
            expires_at_unix=int(time.time()) + 3600,
        )
        with trust_anchor(pub, replay=True):
            decision = contract.authorize(
                root,
                binding_assertion=binding,
                dispatch_assertion=dispatch,
                request_json=json.dumps({"classes": ["shell"], "tool": "shell.local"}),
            )
        assert not decision.allowed
        assert decision.reason == "UNTRUSTED_OVERRIDE"


def test_request_binding_mismatch_denies_reuse() -> None:
    """Round 6: signed dispatch for request A must DENY when reused with request B."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        write_schema(root)
        priv, pub = fixtures.generate_keypair(base / "keys")
        _, _, digest = contract.load_effective_state(root)
        request_a = {"target": "bucket-a", "op": "put"}
        request_b = {"target": "bucket-b", "op": "put"}
        binding = base / "binding.json"
        dispatch = base / "dispatch.json"
        fixtures.write_binding_assertion(
            binding,
            private_key=priv,
            public_key=pub,
            profile="production_write",
            policy_digest=digest,
            authority_permission="admin",
            approved_classes=["production_write", "destructive", "external_write", "network"],
        )
        fixtures.write_dispatch_assertion(
            dispatch,
            private_key=priv,
            public_key=pub,
            tool_id="production.write",
            classes=contract.DEFAULT_TOOL_REGISTRY["production.write"],
            policy_digest=digest,
            request_payload=request_a,
            dispatch_id="prod-1",
            expires_at_unix=int(time.time()) + 3600,
        )
        with trust_anchor(pub, replay=True):
            ok = contract.authorize(
                root,
                binding_assertion=binding,
                dispatch_assertion=dispatch,
                request_json=json.dumps(request_a),
            )
            reused = contract.authorize(
                root,
                binding_assertion=binding,
                dispatch_assertion=dispatch,
                request_json=json.dumps(request_b),
            )
        assert ok.allowed, ok.reason
        assert not reused.allowed
        assert reused.reason == "REQUEST_BINDING_MISMATCH"


def test_duplicate_same_request_high_risk_dispatch_is_replay() -> None:
    """P1A-REPLAY-001: same signed high-risk dispatch + same request must not ALLOW twice."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        write_schema(root)
        priv, pub = fixtures.generate_keypair(base / "keys")
        _, _, digest = contract.load_effective_state(root)
        request = {"target": "bucket-a", "op": "put"}
        binding = base / "binding.json"
        dispatch = base / "dispatch.json"
        fixtures.write_binding_assertion(
            binding,
            private_key=priv,
            public_key=pub,
            profile="production_write",
            policy_digest=digest,
            authority_permission="admin",
            approved_classes=["production_write", "destructive", "external_write", "network"],
        )
        fixtures.write_dispatch_assertion(
            dispatch,
            private_key=priv,
            public_key=pub,
            tool_id="production.write",
            classes=contract.DEFAULT_TOOL_REGISTRY["production.write"],
            policy_digest=digest,
            request_payload=request,
            dispatch_id="prod-replay-1",
            expires_at_unix=int(time.time()) + 3600,
        )
        with trust_anchor(pub, replay=True):
            first = contract.authorize(
                root,
                binding_assertion=binding,
                dispatch_assertion=dispatch,
                request_json=json.dumps(request),
            )
            second = contract.authorize(
                root,
                binding_assertion=binding,
                dispatch_assertion=dispatch,
                request_json=json.dumps(request),
            )
        assert first.allowed, first.reason
        assert not second.allowed
        assert second.reason == "REPLAY"


def test_high_risk_without_replay_boundary_unavailable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        write_schema(root)
        priv, pub = fixtures.generate_keypair(base / "keys")
        _, _, digest = contract.load_effective_state(root)
        request = {"target": "prod", "op": "write"}
        binding = base / "binding.json"
        dispatch = base / "dispatch.json"
        fixtures.write_binding_assertion(
            binding,
            private_key=priv,
            public_key=pub,
            profile="production_write",
            policy_digest=digest,
            authority_permission="admin",
            approved_classes=["production_write", "destructive", "external_write", "network"],
        )
        fixtures.write_dispatch_assertion(
            dispatch,
            private_key=priv,
            public_key=pub,
            tool_id="production.write",
            classes=contract.DEFAULT_TOOL_REGISTRY["production.write"],
            policy_digest=digest,
            request_payload=request,
            dispatch_id="prod-2",
            expires_at_unix=int(time.time()) + 3600,
        )
        with trust_anchor(pub, replay=False):
            decision = contract.authorize(
                root,
                binding_assertion=binding,
                dispatch_assertion=dispatch,
                request_json=json.dumps(request),
            )
        assert not decision.allowed
        assert decision.reason == "BOUNDARY_UNAVAILABLE"


def test_self_minted_readonly_assertions_denied_on_production_cli() -> None:
    """Round 4: read-only self-signed artifacts are still untrusted on production authorize."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        write_schema(root)
        session = base / "atk-session"
        session.mkdir()
        priv, pub = fixtures.generate_keypair(session / "keys")
        _, _, digest = contract.load_effective_state(root)
        request = {"target": "prod", "op": "write"}
        binding = session / "binding.json"
        dispatch = session / "dispatch.json"
        fixtures.write_binding_assertion(
            binding,
            private_key=priv,
            public_key=pub,
            profile="production_write",
            policy_digest=digest,
            authority_permission="admin",
            approved_classes=["production_write", "destructive", "external_write", "network"],
        )
        fixtures.write_dispatch_assertion(
            dispatch,
            private_key=priv,
            public_key=pub,
            tool_id="production.write",
            classes=contract.DEFAULT_TOOL_REGISTRY["production.write"],
            policy_digest=digest,
            request_payload=request,
            dispatch_id="atk-ro-1",
            expires_at_unix=int(time.time()) + 3600,
        )
        # Attacker makes a perfectly read-only, internally consistent session tree.
        os.chmod(binding, 0o444)
        os.chmod(dispatch, 0o444)
        os.chmod(pub, 0o444)
        os.chmod(priv, 0o400)
        os.chmod(session / "keys", 0o555)
        os.chmod(session, 0o555)
        env = dict(os.environ)
        env[contract.TRUST_ANCHOR_ENV] = str(pub)
        denied = run_cli(
            "authorize",
            "--root",
            str(root),
            "--binding-assertion",
            str(binding),
            "--dispatch-assertion",
            str(dispatch),
            "--request-json",
            json.dumps(request),
            env=env,
        )
        assert denied.returncode != 0
        assert "REASON=BOUNDARY_UNAVAILABLE" in denied.stdout
        assert "DECISION=ALLOW" not in denied.stdout


def test_unsupported_platform_authorize_is_boundary_unavailable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        write_schema(root)
        priv, pub = fixtures.generate_keypair(base / "keys")
        _, _, digest = contract.load_effective_state(root)
        request = {"op": "echo"}
        binding = base / "binding.json"
        dispatch = base / "dispatch.json"
        fixtures.write_binding_assertion(
            binding,
            private_key=priv,
            public_key=pub,
            profile="repo_write",
            policy_digest=digest,
            authority_permission="write",
        )
        fixtures.write_dispatch_assertion(
            dispatch,
            private_key=priv,
            public_key=pub,
            tool_id="shell.local",
            classes=contract.DEFAULT_TOOL_REGISTRY["shell.local"],
            policy_digest=digest,
            request_payload=request,
        )
        previous = sys.platform
        with trust_anchor(pub):
            try:
                sys.platform = "darwin"
                decision = contract.authorize(
                    root,
                    binding_assertion=binding,
                    dispatch_assertion=dispatch,
                    request_json=json.dumps(request),
                )
            finally:
                sys.platform = previous
        assert not decision.allowed
        assert decision.reason == "BOUNDARY_UNAVAILABLE"


def test_repo_policy_cannot_broaden() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        write_schema(root)
        write_contract(
            root,
            {
                "version": 1,
                "profiles": {
                    "repo_write": {
                        "allowed": sorted(contract.ACTION_CLASSES),
                        "denied": [],
                        "require_approval": [],
                    }
                },
            },
        )
        report = contract.check_contract(root)
        assert report["result"] == "FAIL"
        assert any(item["code"] == "PROFILE_BROADEN" for item in report["findings"])


def test_dispatch_reservation_is_effect_bound() -> None:
    import threading

    contract._TEST_REPLAY_BOUNDARY_AVAILABLE = True
    contract._TEST_REPLAY_STORE = set()
    contract._TEST_DISPATCH_RESERVATIONS = {}
    try:
        effect = "cd" * 32
        barrier = threading.Barrier(2)
        results: dict[str, str] = {}

        def attempt(name: str) -> None:
            barrier.wait()
            results[name] = contract.reserve_dispatch("race-dispatch", effect, name)

        threads = [
            threading.Thread(target=attempt, args=("attempt-a",)),
            threading.Thread(target=attempt, args=("attempt-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results.values()) == ["conflict", "reserved"]
        owner = next(name for name, value in results.items() if value == "reserved")
        assert contract.consume_dispatch_once("race-dispatch") == "replay"
        assert contract.finalize_dispatch("race-dispatch", effect, owner) == "ok"
        other = "attempt-b" if owner == "attempt-a" else "attempt-a"
        assert contract.finalize_dispatch("race-dispatch", effect, other) == "conflict"
        assert contract.reserve_dispatch("race-dispatch", "ee" * 32, "attempt-c") == "conflict"
    finally:
        contract._TEST_REPLAY_BOUNDARY_AVAILABLE = None
        contract._TEST_REPLAY_STORE = None
        contract._TEST_DISPATCH_RESERVATIONS = None


def main() -> int:
    test_production_cli_is_verification_only()
    test_authorize_without_host_trust_anchor_is_boundary_unavailable()
    test_caller_env_trust_anchor_does_not_alter_authorization()
    test_missing_body_and_hook_kind_and_digest()
    test_authorize_with_fixture_assertions()
    test_caller_cannot_self_promote_with_own_keypair_against_pinned_anchor()
    test_request_json_cannot_under_classify()
    test_request_binding_mismatch_denies_reuse()
    test_duplicate_same_request_high_risk_dispatch_is_replay()
    test_high_risk_without_replay_boundary_unavailable()
    test_self_minted_readonly_assertions_denied_on_production_cli()
    test_unsupported_platform_authorize_is_boundary_unavailable()
    test_repo_policy_cannot_broaden()
    test_dispatch_reservation_is_effect_bound()
    print("SKILLS_CONTRACT_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
