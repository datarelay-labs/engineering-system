#!/usr/bin/env python3
"""Regressions for security hardening desired-state / dry-run / apply gate."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "security-hardening.py"
PROFILE_TOOL = ROOT / "tools" / "security-profile.py"
SCHEMA = json.loads(
    (ROOT / "schemas" / "security-hardening-plan.schema.json").read_text(encoding="utf-8")
)
VALIDATOR = Draft202012Validator(SCHEMA)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"FAIL cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HARDENING = load_module(TOOL, "security_hardening")
PROFILE = load_module(PROFILE_TOOL, "security_profile")


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def write_project(
    root: Path,
    *,
    maturity: str,
    production_oriented: bool | None,
) -> None:
    operations: dict[str, object] = {}
    if production_oriented is not None:
        operations["production_oriented"] = production_oriented
    document = {
        "engineering_system": {"version": "1.6.5"},
        "project": {"name": "fixture", "type": "app", "maturity": maturity},
        "domains": ["standards"],
        "operations": operations,
    }
    path = root / ".engineering"
    path.mkdir(parents=True, exist_ok=True)
    (path / "project.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")


def write_origin(root: Path, slug: str) -> None:
    git = root / ".git"
    git.mkdir(parents=True, exist_ok=True)
    (git / "config").write_text(
        '[remote "origin"]\n'
        f"\turl = https://github.com/{slug}.git\n",
        encoding="utf-8",
    )


def enabled_fixture(**extra: object) -> dict[str, object]:
    fixture: dict[str, object] = {
        "repository": "example/app",
        "visibility": "private",
        "secret_scanning": "enabled",
        "push_protection": "enabled",
        "dependabot_security_updates": "enabled",
        "codeql_default_setup": "configured",
        "privileged_tools": [],
    }
    fixture.update(extra)
    return fixture


def disabled_fixture(**extra: object) -> dict[str, object]:
    return enabled_fixture(
        secret_scanning="disabled",
        push_protection="disabled",
        dependabot_security_updates="disabled",
        codeql_default_setup="not-configured",
        **extra,
    )


def action_of(plan: dict, control_id: str) -> str:
    matches = [item["action"] for item in plan["controls"] if item["id"] == control_id]
    if len(matches) != 1:
        fail(f"{control_id} missing from plan")
    return matches[0]


def write_fixture(path: Path, fixture: dict[str, object]) -> None:
    path.write_text(json.dumps(fixture), encoding="utf-8")


def production_root(base: Path) -> Path:
    root = base / "prod"
    root.mkdir()
    write_project(root, maturity="production", production_oriented=True)
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    write_origin(root, "example/app")
    return root


def test_production_gaps_plan_and_eligibility() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = production_root(Path(tmp))
        fixture = disabled_fixture()
        plan = HARDENING.build_plan(
            root,
            fixture,
            allowed_controls=[
                "secret_scanning",
                "push_protection",
                "dependabot_security_updates",
                "codeql_or_sast",
            ],
        )
        VALIDATOR.validate(plan)
        if plan["profile"] != "production-code":
            fail(f"production profile was {plan['profile']}")
        if plan["mode"] != "DRY_RUN" or plan["network"] != "NONE":
            fail("plan must be dry-run / network-free")
        for control_id in (
            "secret_scanning",
            "push_protection",
            "dependabot_security_updates",
            "codeql_or_sast",
        ):
            if action_of(plan, control_id) != "ENABLE":
                fail(f"{control_id} action was {action_of(plan, control_id)}")
        if not plan["eligible_apply"]:
            fail(f"expected eligible apply, blockers={plan['blockers']}")
        if len(plan["planned_mutations"]) != 4:
            fail(f"planned mutations were {plan['planned_mutations']}")
        if plan["mutation"] != "PLANNED":
            fail(f"mutation flag was {plan['mutation']}")


def test_idempotent_already_compliant() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = production_root(Path(tmp))
        plan = HARDENING.build_plan(
            root,
            enabled_fixture(),
            allowed_controls=["secret_scanning", "push_protection"],
        )
        VALIDATOR.validate(plan)
        if plan["planned_mutations"]:
            fail(f"compliant plan mutated {plan['planned_mutations']}")
        if not plan["eligible_apply"]:
            fail(f"compliant plan blocked: {plan['blockers']}")
        if plan["mutation"] != "NONE":
            fail("compliant dry-run should not mark PLANNED")
        if action_of(plan, "secret_scanning") != "NOOP":
            fail("secret_scanning should be NOOP when enabled")


def test_docs_does_not_force_codeql() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "docs"
        root.mkdir()
        write_project(root, maturity="development", production_oriented=False)
        (root / "mkdocs.yml").write_text("site_name: docs\n", encoding="utf-8")
        (root / "docs").mkdir()
        (root / "docs" / "index.md").write_text("# Docs\n", encoding="utf-8")
        write_origin(root, "example/app")
        plan = HARDENING.build_plan(
            root,
            disabled_fixture(),
            allowed_controls=["secret_scanning"],
        )
        VALIDATOR.validate(plan)
        if plan["profile"] != "docs-site":
            fail(f"docs profile was {plan['profile']}")
        codeql = next(item for item in plan["controls"] if item["id"] == "codeql_or_sast")
        if codeql["action"] != "NOOP" or codeql["desired"] != "not_applicable":
            fail(f"docs CodeQL decision was {codeql}")
        if any(item["id"] == "codeql_or_sast" for item in plan["planned_mutations"]):
            fail("docs plan forced CodeQL mutation")


def test_empty_preproduct_is_deferred_noop() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "empty"
        root.mkdir()
        write_project(root, maturity="experimental", production_oriented=False)
        (root / "README.md").write_text("bootstrap\n", encoding="utf-8")
        write_origin(root, "example/app")
        plan = HARDENING.build_plan(
            root,
            enabled_fixture(bootstrap="allow-no-tests"),
            allowed_controls=["secret_scanning"],
        )
        VALIDATOR.validate(plan)
        if plan["profile"] != "empty-preproduct":
            fail(f"empty profile was {plan['profile']}")
        if any(item["action"] == "ENABLE" for item in plan["controls"]):
            fail("preproduct plan enabled controls")
        if plan["planned_mutations"]:
            fail("preproduct planned mutations should be empty")


def test_needs_input_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "needs"
        root.mkdir()
        (root / "README.md").write_text("no facts\n", encoding="utf-8")
        write_origin(root, "example/app")
        plan = HARDENING.build_plan(root, enabled_fixture(), allowed_controls=["secret_scanning"])
        VALIDATOR.validate(plan)
        if plan["profile"] != "NEEDS_INPUT":
            fail(f"needs-input profile was {plan['profile']}")
        if plan["eligible_apply"]:
            fail("NEEDS_INPUT must not be eligible")
        if action_of(plan, "secret_scanning") != "BLOCK":
            fail("NEEDS_INPUT controls must BLOCK")


def test_unsupported_allowed_control_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = production_root(Path(tmp))
        plan = HARDENING.build_plan(
            root,
            enabled_fixture(),
            allowed_controls=["ruleset_protection"],
        )
        VALIDATOR.validate(plan)
        if plan["eligible_apply"]:
            fail("unsupported allowed control must block eligibility")
        if not any("unsupported" in item for item in plan["blockers"]):
            fail(f"missing unsupported blocker: {plan['blockers']}")


def test_ambiguous_github_facts_block() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = production_root(Path(tmp))
        plan = HARDENING.build_plan(
            root,
            enabled_fixture(secret_scanning="unavailable"),
            allowed_controls=["secret_scanning"],
        )
        VALIDATOR.validate(plan)
        if action_of(plan, "secret_scanning") != "BLOCK":
            fail("unavailable secret scanning must BLOCK")
        if plan["eligible_apply"]:
            fail("ambiguous facts must not be eligible")


def test_foreign_fixture_blocks_apply() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = production_root(Path(tmp))
        plan = HARDENING.build_plan(
            root,
            disabled_fixture(repository="other/app"),
            allowed_controls=["secret_scanning"],
        )
        VALIDATOR.validate(plan)
        if plan["binding"] != "mismatch":
            fail(f"binding was {plan['binding']}")
        if plan["eligible_apply"]:
            fail("foreign fixture must not be eligible")


def test_stale_plan_digest_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = production_root(Path(tmp))
        fixture_path = Path(tmp) / "fixture.json"
        write_fixture(fixture_path, disabled_fixture())
        fixture = PROFILE.load_fixture(fixture_path)
        first = HARDENING.build_plan(
            root, fixture, allowed_controls=["secret_scanning"]
        )
        # Replay with changed facts must not accept the old digest.
        write_fixture(fixture_path, enabled_fixture())
        fresh = PROFILE.load_fixture(fixture_path)
        try:
            HARDENING.apply_plan(
                root,
                fresh,
                allowed_controls=["secret_scanning"],
                execute=False,
                expect_plan_digest=first["plan_digest"],
            )
        except HARDENING.HardeningError as exc:
            if "STALE_PLAN" not in str(exc):
                fail(f"unexpected stale error: {exc}")
        else:
            fail("stale plan digest was accepted")


def test_execute_record_backend_is_idempotent_and_bounded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = production_root(Path(tmp))
        mutator = HARDENING.RecordingMutator()
        allowed = [
            "secret_scanning",
            "push_protection",
            "dependabot_security_updates",
            "codeql_or_sast",
        ]
        result = HARDENING.apply_plan(
            root,
            disabled_fixture(),
            allowed_controls=allowed,
            execute=True,
            mutator=mutator,
        )
        VALIDATOR.validate(result)
        if not result["eligible_apply"]:
            fail(f"execute blocked unexpectedly: {result['blockers']}")
        if result["mode"] != "EXECUTE" or result["mutation"] != "APPLIED":
            fail(f"execute result was mode={result['mode']} mutation={result['mutation']}")
        if result["network"] != "NONE":
            fail("record backend must remain network-free")
        if len(mutator.calls) != 4:
            fail(f"mutator calls were {mutator.calls}")
        # Second execute against already-enabled fixture is NOOP.
        mutator2 = HARDENING.RecordingMutator()
        again = HARDENING.apply_plan(
            root,
            enabled_fixture(),
            allowed_controls=allowed,
            execute=True,
            mutator=mutator2,
        )
        VALIDATOR.validate(again)
        if again["planned_mutations"] or mutator2.calls:
            fail("idempotent execute mutated an already-compliant repo")


def test_execute_blocked_without_eligibility() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = production_root(Path(tmp))
        mutator = HARDENING.RecordingMutator()
        result = HARDENING.apply_plan(
            root,
            disabled_fixture(),
            allowed_controls=[],
            execute=True,
            mutator=mutator,
        )
        VALIDATOR.validate(result)
        if result["eligible_apply"]:
            fail("empty allowed set with required gaps must be ineligible")
        if mutator.calls:
            fail("blocked execute still called mutator")
        if result["mutation"] != "NONE":
            fail("blocked execute must not claim APPLIED")


def test_cli_dry_run_default_and_commands() -> None:
    if HARDENING.command_names() != ("plan", "apply"):
        fail(f"unexpected commands {HARDENING.command_names()}")
    with tempfile.TemporaryDirectory() as tmp:
        root = production_root(Path(tmp))
        fixture = Path(tmp) / "fixture.json"
        write_fixture(fixture, disabled_fixture())
        plan = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "plan",
                "--root",
                str(root),
                "--github-fixture",
                str(fixture),
                "--allowed-control",
                "secret_scanning",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if plan.returncode != 0:
            fail(plan.stderr)
        document = json.loads(plan.stdout)
        VALIDATOR.validate(document)
        if document["mode"] != "DRY_RUN" or document["network"] != "NONE":
            fail("CLI plan escaped dry-run")
        apply = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "apply",
                "--root",
                str(root),
                "--github-fixture",
                str(fixture),
                "--allowed-control",
                "secret_scanning",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if apply.returncode != 0:
            fail(apply.stderr)
        applied = json.loads(apply.stdout)
        if applied["mode"] != "DRY_RUN" or applied["mutation"] not in {"NONE", "PLANNED"}:
            fail("apply default must remain dry-run")
        blocked = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "apply",
                "--root",
                str(root),
                "--github-fixture",
                str(fixture),
                "--execute",
                "--mutation-backend",
                "record",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if blocked.returncode != 2:
            fail(f"ineligible execute exit was {blocked.returncode}: {blocked.stderr}")


def test_no_credential_persistence_tokens() -> None:
    source = TOOL.read_text(encoding="utf-8")
    for banned in ("Authorization", "GITHUB_TOKEN=", "password", "client_secret"):
        if banned in source:
            fail(f"hardening tool contains credential token {banned}")
    # Record backend path must not invent network.
    if "api.github.com" in source and "GhApiMutator" not in source:
        fail("unexpected bare api.github.com usage")


def main() -> None:
    test_production_gaps_plan_and_eligibility()
    test_idempotent_already_compliant()
    test_docs_does_not_force_codeql()
    test_empty_preproduct_is_deferred_noop()
    test_needs_input_blocks()
    test_unsupported_allowed_control_blocks()
    test_ambiguous_github_facts_block()
    test_foreign_fixture_blocks_apply()
    test_stale_plan_digest_rejected()
    test_execute_record_backend_is_idempotent_and_bounded()
    test_execute_blocked_without_eligibility()
    test_cli_dry_run_default_and_commands()
    test_no_credential_persistence_tokens()
    print("PASS security hardening desired-state and safe-apply gate")


if __name__ == "__main__":
    main()
