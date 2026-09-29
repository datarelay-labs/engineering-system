#!/usr/bin/env python3
"""Regression tests for deterministic Engineering System adoption bootstrap."""
from __future__ import annotations

import hashlib
import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ADOPT = ROOT / "tools" / "adopt.py"
CHECK = ROOT / "tools" / "check-adoption.py"
UPGRADE = ROOT / "tools" / "upgrade-adoption.py"
BASELINE = "a" * 40
NEW_BASELINE = "b" * 40
CONTEXT_EPOCH_BASELINE = "cdc54b3220b5ec38e84dc2c33bd500b35edd6b39"
TRUST_HELPER_BASELINE = "dfe9b2c5ad47cc2e4ef6563717a7722635251fe9"


def run(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
    )


def init_repo(root: Path) -> None:
    run("git", "init", "-q", str(root))
    run("git", "config", "user.email", "test@example.invalid", cwd=root)
    run("git", "config", "user.name", "Adoption Test", cwd=root)


def commit_all(root: Path, message: str = "fixture") -> None:
    run("git", "add", ".", cwd=root)
    run("git", "commit", "-qm", message, cwd=root)


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_clean_python_bootstrap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-python"
        target.mkdir()
        init_repo(target)
        (target / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
        (target / "tests").mkdir()
        (target / "tests" / "test_demo.py").write_text("def test_demo():\n    assert True\n", encoding="utf-8")
        commit_all(target)

        audit = run(sys.executable, str(ADOPT), "--root", str(target), "--audit")
        assert "PROJECT_TYPE=python" in audit.stdout
        assert "python -m pytest -q" in audit.stdout
        assert "DOMAIN_CANDIDATES=" in audit.stdout
        assert "OPERATIONS_SIGNALS=<none>" in audit.stdout

        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "python -m pytest -q",
            "--preflight-command",
            "python -m compileall -q .",
            "--release-command",
            "python -m pytest -q",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout
        assert "OPERATIONS_MODE=nonproduction" in applied.stdout
        assert "MERGE_GATE_ENFORCEMENT=unknown" in applied.stdout

        required = (
            "AGENTS.md",
            ".engineering/project.yaml",
            ".engineering/tests.yaml",
            ".engineering/release.yaml",
            ".cursor/rules/engineering-system.mdc",
            ".cursorignore",
            ".cursor/commands/resume.md",
            ".cursor/commands/work-resume.md",
            ".github/ISSUE_TEMPLATE/ai-work-packet.md",
            ".github/workflows/engineering-system.yml",
            ".github/workflows/engineering-release.yml",
            "tools/implementation_preflight.py",
        )
        for rel in required:
            assert (target / rel).is_file(), rel

        project = load_yaml(target / ".engineering/project.yaml")
        engineering = project["engineering_system"]
        assert engineering["version"] == "1.6.5"
        assert engineering["mode"] == "adopted"
        assert engineering["ci_mode"] == "shared"
        assert engineering["baseline"] == BASELINE
        assert engineering["native_ci_workflows"] == []
        assert engineering["merge_gate_status"] == "unknown"
        assert project["operations"]["production_oriented"] is False
        assert project["operations"]["incident_response_required"] is False

        resume_text = (target / ".cursor/commands/resume.md").read_text(encoding="utf-8")
        assert resume_text == (target / ".cursor/commands/work-resume.md").read_text(encoding="utf-8")
        assert "WORK_PACKET_PROVENANCE_UNTRUSTED" in resume_text
        assert "ENGINEERING_SYSTEM_ADOPTION=INCOMPLETE" in resume_text

        tests_text = (target / ".engineering/tests.yaml").read_text(encoding="utf-8")
        assert "cost: medium" in tests_text
        assert "agent_default: true" in tests_text
        assert "ENGINEERING_BASE_REF" in tests_text
        assert "origin/main...HEAD" in tests_text
        assert tests_text.count('command: "git diff --check"') == 0
        assert 'setup_command: "python -m pip install -e . && python -m pip install pytest"' in tests_text

        workflow_text = (target / ".github/workflows/engineering-system.yml").read_text(encoding="utf-8")
        assert f"adoption-compliance.yml@{BASELINE}" in workflow_text
        assert f"enforcement-check.yml@{BASELINE}" in workflow_text
        assert f"affected-tests.yml@{BASELINE}" in workflow_text

        release_workflow = (target / ".github/workflows/engineering-release.yml").read_text(encoding="utf-8")
        assert f"release-contract.yml@{BASELINE}" in release_workflow
        assert "${{ inputs.expected_sha }}" in release_workflow
        assert "${{ inputs.phase }}" in release_workflow

        checked = run(sys.executable, str(CHECK), "--root", str(target))
        assert "ENGINEERING_SYSTEM_ADOPTION=PASS" in checked.stdout


def test_rule_review_is_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-rules"
        target.mkdir()
        init_repo(target)
        (target / "CLAUDE.md").write_text("# Existing project rule\n", encoding="utf-8")
        (target / "go.mod").write_text("module example.invalid/demo\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)

        result = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            check=False,
        )
        assert result.returncode != 0
        assert "RULE_REVIEW_REQUIRED=CLAUDE.md" in result.stdout
        assert "classify existing rules before apply" in result.stdout


def test_existing_ci_requires_mapping_when_ambiguous() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-native-ci"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/native\n\ngo 1.23\n", encoding="utf-8")
        workflow_dir = target / ".github" / "workflows"
        workflow_dir.mkdir(parents=True)
        for name in ("unit.yml", "integration.yml"):
            (workflow_dir / name).write_text(
                f"name: {name}\non:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: go test ./...\n",
                encoding="utf-8",
            )
        commit_all(target)

        blocked = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            "--ci-mode",
            "native",
            check=False,
        )
        assert blocked.returncode != 0
        assert "NATIVE_CI_MAPPING_REQUIRED=" in blocked.stdout

        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            "--ci-mode",
            "native",
            "--native-ci-workflow",
            ".github/workflows/unit.yml",
            "--merge-gate-status",
            "advisory",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout
        workflow_text = (target / ".github/workflows/engineering-system.yml").read_text(encoding="utf-8")
        assert f"adoption-compliance.yml@{BASELINE}" in workflow_text
        assert "affected-tests.yml@" not in workflow_text

        project = load_yaml(target / ".engineering/project.yaml")
        engineering = project["engineering_system"]
        assert engineering["ci_mode"] == "native"
        assert engineering["native_ci_workflows"] == [".github/workflows/unit.yml"]
        assert engineering["merge_gate_status"] == "advisory"


def test_operations_signals_fail_closed_then_production_profile() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-service"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/service\n\ngo 1.23\n", encoding="utf-8")
        (target / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (target / "RUNBOOK.md").write_text("# Operations Runbook\n", encoding="utf-8")
        commit_all(target)

        blocked = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            check=False,
        )
        assert blocked.returncode != 0
        assert "OPERATIONS_REVIEW_REQUIRED=Dockerfile" in blocked.stdout

        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            "--operations-mode",
            "production",
            "--persistent-state",
            "--runbook-path",
            "RUNBOOK.md",
            "--health-command",
            "true",
            "--backup-command",
            "true",
            "--restore-test-command",
            "true",
            "--upgrade-command",
            "true",
            "--rollback-command",
            "true",
            "--operational-e2e-command",
            "true",
            "--public-smoke-command",
            "true",
            "--full-e2e-passes",
            "2",
        )
        assert "OPERATIONS_MODE=production" in applied.stdout

        project = load_yaml(target / ".engineering/project.yaml")
        assert project["operations"]["production_oriented"] is True
        assert project["operations"]["runbook_required"] is True
        assert project["operations"]["incident_response_required"] is True
        assert project["operations"]["persistent_state"] is True
        assert project["operations"]["runbook_paths"] == ["RUNBOOK.md"]
        assert project["operations"]["health_command"] == "true"
        assert project["operations"]["backup_command"] == "true"
        assert project["operations"]["restore_test_command"] == "true"

        release = load_yaml(target / ".engineering/release.yaml")
        assert release["operational_e2e_required"] is True
        assert release["operational_e2e_command"] == "true"
        assert release["full_e2e_passes"] == 2
        assert release["public_smoke_required"] is True
        assert release["public_smoke_command"] == "true"
        release_workflow = (target / ".github/workflows/engineering-release.yml").read_text(encoding="utf-8")
        assert f"release-contract.yml@{BASELINE}" in release_workflow

        checked = run(sys.executable, str(CHECK), "--root", str(target))
        assert "ENGINEERING_SYSTEM_ADOPTION=PASS" in checked.stdout


def test_quality_and_domain_discovery() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-node"
        target.mkdir()
        init_repo(target)
        (target / "package.json").write_text(
            '{"scripts":{"test":"vitest run","build":"vite build","lint":"eslint .","typecheck":"tsc --noEmit"}}\n',
            encoding="utf-8",
        )
        (target / "package-lock.json").write_text("{}\n", encoding="utf-8")
        (target / "server").mkdir()
        (target / "client").mkdir()
        (target / "tests").mkdir()
        commit_all(target)

        audit = run(sys.executable, str(ADOPT), "--root", str(target), "--audit")
        assert '"build": "npm run build"' in audit.stdout
        assert '"lint": "npm run lint"' in audit.stdout
        assert '"typecheck": "npm run typecheck"' in audit.stdout
        assert '"server"' in audit.stdout
        assert '"client"' in audit.stdout

        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "npm test",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout

        project = load_yaml(target / ".engineering/project.yaml")
        assert {"server", "client"}.issubset(set(project["domains"]))

        tests = load_yaml(target / ".engineering/tests.yaml")
        ids = {scenario["id"] for scenario in tests["scenarios"]}
        assert "ADOPTED-BUILD-001" in ids
        assert "ADOPTED-LINT-001" in ids
        assert "ADOPTED-TYPECHECK-001" in ids


def test_managed_upgrade_to_1_6() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-upgrade"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/upgrade\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)

        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )

        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.5.0"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")

        workflow_path = target / ".github/workflows/engineering-system.yml"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        enforcement_block = (
            "\n  enforcement-reconcile:\n"
            f"    uses: datarelay-labs/engineering-system/.github/workflows/enforcement-check.yml@{BASELINE}\n"
        )
        workflow_text = workflow_text.replace(enforcement_block, "")
        workflow_path.write_text(workflow_text, encoding="utf-8")

        history = ROOT / "tools" / "managed_adapter_history" / "resume" / "1.6.1.md"
        managed_prior = history.read_text(encoding="utf-8")
        (target / ".cursor/commands/resume.md").write_text(managed_prior, encoding="utf-8")
        (target / ".cursor/commands/work-resume.md").write_text(managed_prior, encoding="utf-8")
        prior_rule = (ROOT / "tools" / "managed_adapter_history" / "rule" / "1.6.3.mdc").read_text(encoding="utf-8")
        (target / ".cursor/rules/engineering-system.mdc").write_text(prior_rule, encoding="utf-8")
        (target / ".cursorignore").unlink()
        commit_all(target, "downgrade fixture to 1.5")

        upgraded = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        assert "CURSOR_RULE_SYNCED=YES" in upgraded.stdout
        assert "CURSORIGNORE_INSTALLED=YES" in upgraded.stdout
        assert "CURSOR_RESUME_ADAPTERS_SYNCED=.cursor/commands/resume.md,.cursor/commands/work-resume.md" in upgraded.stdout

        upgraded_project = load_yaml(project_path)
        assert upgraded_project["engineering_system"]["version"] == "1.6.5"
        assert upgraded_project["engineering_system"]["baseline"] == NEW_BASELINE
        upgraded_workflow = workflow_path.read_text(encoding="utf-8")
        assert f"adoption-compliance.yml@{NEW_BASELINE}" in upgraded_workflow
        assert f"enforcement-check.yml@{NEW_BASELINE}" in upgraded_workflow
        assert f"affected-tests.yml@{NEW_BASELINE}" in upgraded_workflow

        canonical_resume = (ROOT / "templates" / ".cursor" / "commands" / "resume.md").read_text(encoding="utf-8")
        assert (target / ".cursor/commands/resume.md").read_text(encoding="utf-8") == canonical_resume
        assert (target / ".cursor/commands/work-resume.md").read_text(encoding="utf-8") == canonical_resume
        canonical_rule = (ROOT / "templates" / ".cursor" / "rules" / "engineering-system.mdc").read_text(encoding="utf-8")
        assert (target / ".cursor/rules/engineering-system.mdc").read_text(encoding="utf-8") == canonical_rule
        assert (target / ".cursorignore").read_text(encoding="utf-8") == (ROOT / "templates" / ".cursorignore").read_text(encoding="utf-8")


def test_same_version_1_6_5_pre_context_epoch_upgrade() -> None:
    """A legitimate pre-context-epoch 1.6.5 adoption must remain upgradeable."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-165-baseline-upgrade"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/same-version\n\ngo 1.23\n",
            encoding="utf-8",
        )
        commit_all(target)

        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )

        resume_history = (
            ROOT
            / "tools"
            / "managed_adapter_history"
            / "resume"
            / "1.6.5-pre-context-epoch.md"
        ).read_text(encoding="utf-8")
        rule_history = (
            ROOT
            / "tools"
            / "managed_adapter_history"
            / "rule"
            / "1.6.5-pre-context-epoch.mdc"
        ).read_text(encoding="utf-8")
        (target / ".cursor/commands/resume.md").write_text(
            resume_history, encoding="utf-8"
        )
        (target / ".cursor/commands/work-resume.md").write_text(
            resume_history, encoding="utf-8"
        )
        (target / ".cursor/rules/engineering-system.mdc").write_text(
            rule_history, encoding="utf-8"
        )
        (target / "tools/context_epoch.py").unlink()
        (target / "tools/implementation_preflight.py").unlink()
        commit_all(target, "simulate canonical pre-context-epoch 1.6.5 baseline")

        upgraded = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        assert "CURSOR_RULE_SYNCED=YES" in upgraded.stdout
        assert (
            "CURSOR_RESUME_ADAPTERS_SYNCED="
            ".cursor/commands/resume.md,.cursor/commands/work-resume.md"
            in upgraded.stdout
        )
        assert "CONTEXT_EPOCH_INSTALLED=tools/context_epoch.py" in upgraded.stdout
        assert "IMPLEMENTATION_PREFLIGHT_INSTALLED=tools/implementation_preflight.py" in upgraded.stdout

        project = load_yaml(target / ".engineering/project.yaml")
        assert project["engineering_system"]["version"] == "1.6.5"
        assert project["engineering_system"]["baseline"] == NEW_BASELINE
        canonical_resume = (
            ROOT / "templates" / ".cursor" / "commands" / "resume.md"
        ).read_text(encoding="utf-8")
        canonical_rule = (
            ROOT / "templates" / ".cursor" / "rules" / "engineering-system.mdc"
        ).read_text(encoding="utf-8")
        assert (target / ".cursor/commands/resume.md").read_text(
            encoding="utf-8"
        ) == canonical_resume
        assert (target / ".cursor/commands/work-resume.md").read_text(
            encoding="utf-8"
        ) == canonical_resume
        assert (target / ".cursor/rules/engineering-system.mdc").read_text(
            encoding="utf-8"
        ) == canonical_rule
        assert (target / "tools/context_epoch.py").read_bytes() == (
            ROOT / "tools/context_epoch.py"
        ).read_bytes()


def test_same_version_1_6_5_context_epoch_resume_upgrade() -> None:
    """Recreate the real cdc54b3 managed cohort and upgrade it end to end."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-165-context-resume-upgrade"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/same-version-context\n\ngo 1.23\n",
            encoding="utf-8",
        )
        commit_all(target)

        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            CONTEXT_EPOCH_BASELINE,
            "--test-command",
            "go test ./...",
        )

        # Recreate every adoption-managed file tracked by the immutable cdc54b3
        # manifest from the actual canonical Git object, not from a synthetic
        # approximation.  This is the cohort that Atlas PR #82 exposed.
        manifest = (
            ROOT
            / "tools"
            / "managed_adapter_history"
            / "file_hashes"
            / "1.6.5-cdc54b3.sha256"
        )
        historical_paths: list[str] = []
        for raw in manifest.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            digest, rel = line.split(None, 1)
            historical = run(
                "git",
                "show",
                f"{CONTEXT_EPOCH_BASELINE}:{rel}",
                cwd=ROOT,
            ).stdout
            assert hashlib.sha256(historical.encode("utf-8")).hexdigest() == digest
            path = target / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(historical, encoding="utf-8")
            historical_paths.append(rel)

        prior_resume = run(
            "git",
            "show",
            f"{CONTEXT_EPOCH_BASELINE}:templates/.cursor/commands/resume.md",
            cwd=ROOT,
        ).stdout
        resume_history = (
            ROOT
            / "tools"
            / "managed_adapter_history"
            / "resume"
            / "1.6.5-context-epoch-pre-thin-router.md"
        ).read_text(encoding="utf-8")
        assert prior_resume == resume_history
        (target / ".cursor/commands/resume.md").write_text(
            prior_resume, encoding="utf-8"
        )
        (target / ".cursor/commands/work-resume.md").write_text(
            prior_resume, encoding="utf-8"
        )

        # work_packet_authority.py became an adoption-managed skills runtime
        # dependency after cdc54b3.  It must therefore be absent in this exact
        # historical cohort and installed by the upgrade.
        authority = target / "tools/work_packet_authority.py"
        if authority.exists():
            authority.unlink()

        commit_all(target, "simulate exact canonical cdc54b3 managed cohort")

        upgraded = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        assert (
            "CURSOR_RESUME_ADAPTERS_SYNCED="
            ".cursor/commands/resume.md,.cursor/commands/work-resume.md"
            in upgraded.stdout
        )
        assert "tools/context_epoch.py" in upgraded.stdout
        assert "tools/independent_verifier.py" in upgraded.stdout
        assert "tools/skills-contract.py" in upgraded.stdout
        assert "tools/work_packet_authority.py" in upgraded.stdout

        project = load_yaml(target / ".engineering/project.yaml")
        assert project["engineering_system"]["version"] == "1.6.5"
        assert project["engineering_system"]["baseline"] == NEW_BASELINE

        canonical_resume = (
            ROOT / "templates" / ".cursor" / "commands" / "resume.md"
        ).read_text(encoding="utf-8")
        assert (target / ".cursor/commands/resume.md").read_text(
            encoding="utf-8"
        ) == canonical_resume
        assert (target / ".cursor/commands/work-resume.md").read_text(
            encoding="utf-8"
        ) == canonical_resume

        # Every historical managed file now converges to the current canonical
        # bytes, and the newly managed runtime dependency is installed too.
        for rel in historical_paths:
            assert (target / rel).read_bytes() == (ROOT / rel).read_bytes(), rel
        assert authority.read_bytes() == (ROOT / "tools/work_packet_authority.py").read_bytes()


def test_managed_file_hash_manifests_match_immutable_revisions() -> None:
    """Historical hash manifests must be derived from the named canonical commits."""
    cases = (
        ("1.6.5-cdc54b3.sha256", CONTEXT_EPOCH_BASELINE),
        ("1.6.5-dfe9b2c.sha256", TRUST_HELPER_BASELINE),
    )
    history = ROOT / "tools" / "managed_adapter_history" / "file_hashes"
    for name, revision in cases:
        manifest = history / name
        assert manifest.is_file(), name
        for raw in manifest.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            digest, rel = line.split(None, 1)
            historical = run("git", "show", f"{revision}:{rel}", cwd=ROOT).stdout
            assert hashlib.sha256(historical.encode("utf-8")).hexdigest() == digest, (
                name,
                rel,
            )


def test_managed_file_hash_history_upgrades_known_bytes_and_rejects_custom() -> None:
    """Historical managed hashes may upgrade; unknown custom bytes remain fail-closed."""
    upgrade_path = ROOT / "tools" / "upgrade-adoption.py"
    spec = importlib.util.spec_from_file_location("upgrade_adoption_hash_history", upgrade_path)
    assert spec and spec.loader
    upgrade = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upgrade)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        canonical = base / "canonical"
        target = base / "target"
        rel = "tools/example-managed.py"
        current = b"current managed bytes\n"
        prior = b"prior managed bytes\n"
        custom = b"project custom bytes\n"

        (canonical / rel).parent.mkdir(parents=True, exist_ok=True)
        (canonical / rel).write_bytes(current)
        manifest_dir = canonical / "tools/managed_adapter_history/file_hashes"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "fixture.sha256").write_text(
            f"{hashlib.sha256(prior).hexdigest()}  {rel}\n",
            encoding="utf-8",
        )
        (target / rel).parent.mkdir(parents=True, exist_ok=True)

        original = upgrade.CANONICAL
        upgrade.CANONICAL = canonical
        try:
            (target / rel).write_bytes(prior)
            planned = upgrade.plan_managed_file_install(
                target, (rel,), label="fixture"
            )
            assert planned == {rel: current.decode("utf-8")}

            (target / rel).write_bytes(custom)
            try:
                upgrade.plan_managed_file_install(target, (rel,), label="fixture")
            except SystemExit as exc:
                assert "local/custom changes" in str(exc)
            else:
                raise AssertionError("custom managed-file bytes were accepted")
        finally:
            upgrade.CANONICAL = original


def test_custom_cursorignore_preserved_on_upgrade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-custom-ignore"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/custom-ignore\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        run(sys.executable, str(ADOPT), "--root", str(target), "--apply", "--baseline-sha", BASELINE, "--test-command", "go test ./...")
        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.3"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        prior_rule = (ROOT / "tools" / "managed_adapter_history" / "rule" / "1.6.3.mdc").read_text(encoding="utf-8")
        (target / ".cursor/rules/engineering-system.mdc").write_text(prior_rule, encoding="utf-8")
        (target / ".cursorignore").write_text("# custom\nprivate-generated/\n", encoding="utf-8")
        commit_all(target, "custom ignore fixture")
        upgraded = run(sys.executable, str(UPGRADE), "--root", str(target), "--apply", "--baseline-sha", NEW_BASELINE)
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        assert "CURSORIGNORE_INSTALLED=NO" in upgraded.stdout
        assert (target / ".cursorignore").read_text(encoding="utf-8") == "# custom\nprivate-generated/\n"


def test_supported_managed_cursor_rule_history_is_upgradeable() -> None:
    expected = {
        "1.5.0.mdc": "051b2798360b1519d86f5575f98dd1f38d077b7bc866eef80a9e9619f0701626",
        "1.5.1.mdc": "7d13a513dceae8cc96a285044079a3a25cff5422d919a71d9a03cbec771ab506",
        "1.6.0.mdc": "48b933abedf8baeea2ebcce433eb0e1b8de2c82e8727ab23316830b89e6d1503",
        "1.6.0-cursor-rule-routing.mdc": "af8e76084880d2effefb50336a8e02777892d04932852ac5784b47d318196553",
        "1.6.0-work-packet-intent.mdc": "81edcc3137ca63ee5074b0edd0315d3b2ae863fe5b8643d68faacfa5e07eac2a",
        "1.6.3.mdc": "01a357b466549a3bf2e7495fca78ef9a2aaead3b895f3583186e7fa8874732e5",
        "1.6.5-pre-context-epoch.mdc": "49438f2735d2a97b7776a7de6c21deebf5ed6679628bba783ee65468c1e94752",
    }
    history = ROOT / "tools" / "managed_adapter_history" / "rule"
    assert {path.name for path in history.glob("*.mdc")} == set(expected)
    spec = importlib.util.spec_from_file_location("upgrade_adoption", UPGRADE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    canonical = (ROOT / "templates" / ".cursor" / "rules" / "engineering-system.mdc").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        rule = target / ".cursor/rules/engineering-system.mdc"
        rule.parent.mkdir(parents=True)
        for name, digest in expected.items():
            text = (history / name).read_text(encoding="utf-8")
            assert hashlib.sha256(text.encode("utf-8")).hexdigest() == digest
            assert text != canonical
            rule.write_text(text, encoding="utf-8")
            assert module.plan_cursor_rule_update(target) == canonical


def test_custom_cursor_rule_fails_closed_before_upgrade_mutation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-custom-rule"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/custom-rule\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        run(sys.executable, str(ADOPT), "--root", str(target), "--apply", "--baseline-sha", BASELINE, "--test-command", "go test ./...")
        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.3"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        (target / ".cursor/rules/engineering-system.mdc").write_text("# custom cursor rule\n", encoding="utf-8")
        commit_all(target, "custom rule fixture")
        before = _managed_upgrade_file_snapshot(target)
        failed = run(sys.executable, str(UPGRADE), "--root", str(target), "--apply", "--baseline-sha", NEW_BASELINE, check=False)
        assert failed.returncode != 0
        assert "local/custom changes" in failed.stdout
        assert _managed_upgrade_file_snapshot(target) == before


def test_custom_resume_adapter_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-custom-resume"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/custom\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)

        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )

        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.5.0"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")

        workflow_path = target / ".github/workflows/engineering-system.yml"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        enforcement_block = (
            "\n  enforcement-reconcile:\n"
            f"    uses: datarelay-labs/engineering-system/.github/workflows/enforcement-check.yml@{BASELINE}\n"
        )
        workflow_path.write_text(workflow_text.replace(enforcement_block, ""), encoding="utf-8")
        (target / ".cursor/commands/resume.md").write_text("# project-custom resume\n", encoding="utf-8")
        commit_all(target, "custom resume fixture")

        before = _managed_upgrade_file_snapshot(target)

        failed = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert failed.returncode != 0
        assert "local/custom changes" in failed.stdout
        assert (target / ".cursor/commands/resume.md").read_text(encoding="utf-8") == "# project-custom resume\n"
        assert _managed_upgrade_file_snapshot(target) == before


def test_grant_style_baseline_declarations_upgraded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-grant-decls"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/grant\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)

        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )

        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.1"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")

        stale_sha = "f" * 40
        existing_agents = (target / "AGENTS.md").read_text(encoding="utf-8")
        agents = (
            "# DataRelay Grant Repository Engineering Rules\n\n"
            "This repository follows the canonical Data Relay Labs Engineering System:\n"
            "https://github.com/datarelay-labs/engineering-system\n\n"
            f"Adoption baseline: Engineering System version 1.6.1 at immutable commit `{stale_sha}`.\n\n"
            "## Product invariants\n\nKeep project-specific text.\n\n"
            + existing_agents
        )
        readme = (
            "# Grant\n\n"
            "## Engineering\n\n"
            "This repository follows the canonical Data Relay Labs Engineering System.\n\n"
            "The current repository baseline identifies Engineering System **1.6.1** and keeps "
            "patent-described concepts separated.\n"
        )
        (target / "AGENTS.md").write_text(agents, encoding="utf-8")
        (target / "README.md").write_text(readme, encoding="utf-8")
        commit_all(target, "stale grant-style declarations")

        upgraded = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        assert "BASELINE_DECLARATIONS_SYNCED=AGENTS.md,README.md" in upgraded.stdout

        agents_text = (target / "AGENTS.md").read_text(encoding="utf-8")
        readme_text = (target / "README.md").read_text(encoding="utf-8")
        assert "Keep project-specific text." in agents_text
        assert (
            f"Adoption baseline: Engineering System version 1.6.5 at immutable commit `{NEW_BASELINE}`."
            in agents_text
        )
        assert "1.6.1" not in agents_text
        assert stale_sha not in agents_text
        assert (
            "The current repository baseline identifies Engineering System **1.6.5** and keeps"
            in readme_text
        )
        assert "1.6.1" not in readme_text
        assert "patent-described concepts separated." in readme_text


def test_ambiguous_baseline_declaration_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-ambiguous-decls"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/ambiguous\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)

        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )

        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.1"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        (target / "AGENTS.md").write_text(
            "# Custom rules\n\nPinned Engineering System version 1.6.1 for this fork.\n",
            encoding="utf-8",
        )
        commit_all(target, "ambiguous declaration fixture")

        failed = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert failed.returncode != 0
        assert "ambiguous/custom" in failed.stdout
        assert "Pinned Engineering System version 1.6.1" in (target / "AGENTS.md").read_text(
            encoding="utf-8"
        )


def _managed_upgrade_file_snapshot(root: Path) -> dict[str, bytes]:
    rels = (
        ".engineering/project.yaml",
        ".engineering/release.yaml",
        ".engineering/tests.yaml",
        ".github/workflows/engineering-system.yml",
        ".github/workflows/engineering-release.yml",
        "AGENTS.md",
        "README.md",
        ".cursor/commands/resume.md",
        ".cursor/commands/work-resume.md",
        "tools/context_epoch.py",
        "tools/engineering-context.py",
    )
    return {rel: (root / rel).read_bytes() for rel in rels if (root / rel).is_file()}


def test_stale_outside_form_declaration_fails_without_partial_upgrade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-stale-outside-form"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/stale-outside\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)

        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )

        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.1"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")

        existing_agents = (target / "AGENTS.md").read_text(encoding="utf-8")
        (target / "AGENTS.md").write_text(
            "# Stale outside-form fixture\n\n"
            "Adoption baseline: Engineering System version 1.6.1 at immutable commit "
            f"`{BASELINE}`.\n\n"
            "Compatibility note: temporary support for 1.6.1 clients remains during migration.\n\n"
            + existing_agents,
            encoding="utf-8",
        )
        commit_all(target, "stale outside-form declaration fixture")

        before = _managed_upgrade_file_snapshot(target)
        assert "AGENTS.md" in before
        assert ".engineering/project.yaml" in before

        failed = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert failed.returncode != 0
        assert "stale Engineering System version 1.6.1" in failed.stdout
        assert _managed_upgrade_file_snapshot(target) == before


def knowledge_instructions(agents_text: str) -> tuple[str, str]:
    commands = re.findall(r"`(python3 tools/knowledge-contract\.py [^`]*)`", agents_text)
    assert commands == [
        "python3 tools/knowledge-contract.py check",
        "python3 tools/knowledge-contract.py route",
    ]
    return commands[0], commands[1]


def run_knowledge_instruction(root: Path, command: str) -> subprocess.CompletedProcess[str]:
    parts = command.split()
    assert parts[0] == "python3"
    parts[0] = sys.executable
    return run(*parts, cwd=root, check=False)


def assert_installed_knowledge_contract(root: Path) -> None:
    for rel in ("tools/knowledge-contract.py", "schemas/knowledge-index.schema.json"):
        assert (root / rel).read_text(encoding="utf-8") == (ROOT / rel).read_text(encoding="utf-8"), rel
    assert not (root / ".engineering" / "knowledge.yaml").exists()


def assert_absent_index_instructions(root: Path) -> None:
    check_cmd, route_cmd = knowledge_instructions((root / "AGENTS.md").read_text(encoding="utf-8"))
    checked = run_knowledge_instruction(root, check_cmd)
    assert checked.returncode == 0, checked.stdout
    assert "KNOWLEDGE_INDEX=ABSENT" in checked.stdout
    assert "RESULT=PASS" in checked.stdout
    routed = run_knowledge_instruction(root, route_cmd)
    assert routed.returncode == 0, routed.stdout
    assert "RETRIEVAL=LOCAL" in routed.stdout
    assert not (root / ".engineering" / "knowledge.yaml").exists()


def test_optional_knowledge_contract_adoption_and_upgrade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-knowledge"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/knowledge\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout
        assert_installed_knowledge_contract(target)
        assert_absent_index_instructions(target)

        for rel in ("tools/knowledge-contract.py", "schemas/knowledge-index.schema.json"):
            (target / rel).unlink()
        project_path = target / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "drop knowledge contract before upgrade")

        upgraded = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        assert (
            "KNOWLEDGE_CONTRACT_INSTALLED=tools/knowledge-contract.py,schemas/knowledge-index.schema.json"
            in upgraded.stdout
        )
        assert_installed_knowledge_contract(target)
        assert_absent_index_instructions(target)

        (target / "docs").mkdir()
        (target / "docs" / "NOTE.md").write_text("canonical\n", encoding="utf-8")
        index = {
            "version": 1,
            "domains": [
                {"id": "core", "summary": "Product note.", "canonical": ["docs/NOTE.md"]},
            ],
        }
        (target / ".engineering" / "knowledge.yaml").write_text(
            yaml.safe_dump(index, sort_keys=False),
            encoding="utf-8",
        )
        check_cmd, _route_cmd = knowledge_instructions((target / "AGENTS.md").read_text(encoding="utf-8"))
        present = run_knowledge_instruction(target, check_cmd)
        assert present.returncode == 0, present.stdout
        assert "KNOWLEDGE_INDEX=PRESENT" in present.stdout
        assert "RESULT=PASS" in present.stdout

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-custom-knowledge"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/custom-knowledge\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        custom = "#!/usr/bin/env python3\nprint('custom')\n"
        (target / "tools" / "knowledge-contract.py").write_text(custom, encoding="utf-8")
        project_path = target / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "custom knowledge contract")
        before = _managed_upgrade_file_snapshot(target)
        before_tool = (target / "tools" / "knowledge-contract.py").read_bytes()
        failed = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert failed.returncode != 0
        assert "tools/knowledge-contract.py contains local/custom changes" in failed.stdout
        assert _managed_upgrade_file_snapshot(target) == before
        assert (target / "tools" / "knowledge-contract.py").read_bytes() == before_tool
        assert not (target / ".engineering" / "knowledge.yaml").exists()


def test_preexisting_custom_knowledge_contract_fails_closed_before_bootstrap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-custom-bootstrap"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/custom-bootstrap\n\ngo 1.23\n", encoding="utf-8")
        tool = target / "tools" / "knowledge-contract.py"
        tool.parent.mkdir()
        tool.write_text("#!/usr/bin/env python3\nraise SystemExit(7)\n", encoding="utf-8")
        commit_all(target)
        failed = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            check=False,
        )
        assert failed.returncode != 0
        assert "tools/knowledge-contract.py contains local/custom changes" in failed.stdout
        assert "ADOPTION_BOOTSTRAP=PASS" not in failed.stdout
        assert run("git", "status", "--porcelain", cwd=target).stdout == ""
        assert not (target / "AGENTS.md").exists()
        assert not (target / ".engineering" / "project.yaml").exists()
        assert not (target / ".engineering" / "knowledge.yaml").exists()
        assert tool.read_text(encoding="utf-8") == "#!/usr/bin/env python3\nraise SystemExit(7)\n"

        tool.write_text((ROOT / "tools" / "knowledge-contract.py").read_text(encoding="utf-8"), encoding="utf-8")
        commit_all(target, "canonical knowledge contract helper")
        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout
        assert "tools/knowledge-contract.py" in applied.stdout.split("FILES_PRESERVED=", 1)[-1]
        assert_installed_knowledge_contract(target)
        assert_absent_index_instructions(target)


def test_runtime_contract_preserves_existing_authority_commands() -> None:
    health = "printf health"
    smoke = "printf smoke"
    e2e = "printf e2e"

    def assert_authorities(root: Path) -> None:
        project = load_yaml(root / ".engineering" / "project.yaml")
        release = load_yaml(root / ".engineering" / "release.yaml")
        assert project["operations"]["health_command"] == health
        assert release["public_smoke_command"] == smoke
        assert release["operational_e2e_command"] == e2e
        assert not (root / ".engineering" / "runtime.yaml").exists()
        for rel in ("tools/runtime-contract.py", "schemas/runtime-contract.schema.json"):
            assert (root / rel).read_text(encoding="utf-8") == (ROOT / rel).read_text(encoding="utf-8"), rel
        checked = run(
            sys.executable,
            str(ROOT / "tools" / "runtime-contract.py"),
            "check",
            "--root",
            str(root),
            check=False,
        )
        assert checked.returncode == 0, checked.stdout
        assert "RUNTIME_CONTRACT=ABSENT" in checked.stdout
        assert "HEALTH_AUTHORITY=operations.health_command" in checked.stdout
        assert f"HEALTH_COMMAND={health}" in checked.stdout
        assert "SMOKE_AUTHORITY=release.public_smoke_command" in checked.stdout
        assert f"SMOKE_COMMAND={smoke}" in checked.stdout
        assert "E2E_AUTHORITY=release.operational_e2e_command" in checked.stdout
        assert f"E2E_COMMAND={e2e}" in checked.stdout

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-runtime"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/runtime\n\ngo 1.23\n", encoding="utf-8")
        (target / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (target / "RUNBOOK.md").write_text("# Operations Runbook\n", encoding="utf-8")
        commit_all(target)
        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            "--operations-mode",
            "production",
            "--persistent-state",
            "--runbook-path",
            "RUNBOOK.md",
            "--health-command",
            health,
            "--backup-command",
            "true",
            "--restore-test-command",
            "true",
            "--upgrade-command",
            "true",
            "--rollback-command",
            "true",
            "--operational-e2e-command",
            e2e,
            "--public-smoke-command",
            smoke,
            "--full-e2e-passes",
            "1",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout
        assert_authorities(target)

        for rel in ("tools/runtime-contract.py", "schemas/runtime-contract.schema.json"):
            (target / rel).unlink()
        project_path = target / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "drop runtime contract before upgrade")
        upgraded = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        assert (
            "RUNTIME_CONTRACT_INSTALLED=tools/runtime-contract.py,schemas/runtime-contract.schema.json"
            in upgraded.stdout
        )
        assert_authorities(target)

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-custom-runtime"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/custom-runtime\n\ngo 1.23\n", encoding="utf-8")
        (target / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (target / "RUNBOOK.md").write_text("# Operations Runbook\n", encoding="utf-8")
        commit_all(target)
        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            "--operations-mode",
            "production",
            "--runbook-path",
            "RUNBOOK.md",
            "--health-command",
            health,
            "--operational-e2e-command",
            e2e,
            "--public-smoke-command",
            smoke,
            "--full-e2e-passes",
            "1",
        )
        (target / "tools" / "runtime-contract.py").write_text(
            "#!/usr/bin/env python3\nprint('custom')\n",
            encoding="utf-8",
        )
        project_path = target / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "custom runtime contract")
        before = _managed_upgrade_file_snapshot(target)
        before_health = load_yaml(project_path)["operations"]["health_command"]
        failed = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert failed.returncode != 0
        assert "tools/runtime-contract.py contains local/custom changes" in failed.stdout
        assert _managed_upgrade_file_snapshot(target) == before
        assert load_yaml(project_path)["operations"]["health_command"] == before_health
        assert not (target / ".engineering" / "runtime.yaml").exists()


def assert_installed_skills_contract(root: Path) -> None:
    for rel in (
        "tools/skills-contract.py",
        "tools/work_packet_authority.py",
        "schemas/skills-contract.schema.json",
    ):
        assert (root / rel).read_text(encoding="utf-8") == (ROOT / rel).read_text(encoding="utf-8"), rel
    assert not (root / ".engineering" / "skills.yaml").exists()
    # Test-only fixture minting helper must not be adoption-managed.
    assert not (root / "tools" / "skills_contract_fixtures.py").exists()


def test_optional_skills_contract_adoption_and_upgrade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-skills"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/skills\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout
        assert_installed_skills_contract(target)
        agents = (target / "AGENTS.md").read_text(encoding="utf-8")
        assert "skills.yaml" in agents
        assert "tools/skills-contract.py" in agents
        checked = run(
            sys.executable,
            str(target / "tools" / "skills-contract.py"),
            "check",
            "--root",
            str(target),
        )
        assert checked.returncode == 0, checked.stdout
        assert "SKILLS_CONTRACT=ABSENT" in checked.stdout
        assert "RESULT=PASS" in checked.stdout

        for rel in (
            "tools/skills-contract.py",
            "tools/work_packet_authority.py",
            "schemas/skills-contract.schema.json",
        ):
            (target / rel).unlink()
        project_path = target / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "drop skills contract before upgrade")

        upgraded = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        assert (
            "SKILLS_CONTRACT_INSTALLED=tools/skills-contract.py,tools/work_packet_authority.py,schemas/skills-contract.schema.json"
            in upgraded.stdout
        )
        assert_installed_skills_contract(target)

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-custom-skills"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/custom-skills\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        custom = "#!/usr/bin/env python3\nprint('custom-skills')\n"
        (target / "tools" / "skills-contract.py").write_text(custom, encoding="utf-8")
        project_path = target / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        before = run("git", "status", "--porcelain", cwd=target).stdout
        commit_all(target, "custom skills helper")
        before_tool = (target / "tools" / "skills-contract.py").read_bytes()
        failed = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert failed.returncode != 0
        assert "tools/skills-contract.py contains local/custom changes" in failed.stdout
        assert (target / "tools" / "skills-contract.py").read_bytes() == before_tool
        assert not (target / ".engineering" / "skills.yaml").exists()
        del before


def test_preexisting_custom_skills_contract_fails_closed_before_bootstrap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-custom-skills-bootstrap"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/custom-skills-boot\n\ngo 1.23\n", encoding="utf-8")
        tool = target / "tools" / "skills-contract.py"
        tool.parent.mkdir()
        tool.write_text("#!/usr/bin/env python3\nraise SystemExit(7)\n", encoding="utf-8")
        commit_all(target)
        failed = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            check=False,
        )
        assert failed.returncode != 0
        assert "tools/skills-contract.py contains local/custom changes" in failed.stdout
        assert "ADOPTION_BOOTSTRAP=PASS" not in failed.stdout
        assert run("git", "status", "--porcelain", cwd=target).stdout == ""
        assert not (target / "AGENTS.md").exists()
        assert not (target / ".engineering" / "project.yaml").exists()
        assert not (target / ".engineering" / "skills.yaml").exists()

        tool.write_text((ROOT / "tools" / "skills-contract.py").read_text(encoding="utf-8"), encoding="utf-8")
        schema = target / "schemas" / "skills-contract.schema.json"
        schema.parent.mkdir(parents=True, exist_ok=True)
        schema.write_text(
            (ROOT / "schemas" / "skills-contract.schema.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        commit_all(target, "canonical skills contract helper")
        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout
        assert "tools/skills-contract.py" in applied.stdout.split("FILES_PRESERVED=", 1)[-1]
        assert_installed_skills_contract(target)


def test_skills_compliance_reports_missing_referenced_helper() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-skills-compliance"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/skills-compliance\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        dependency = target / "tools" / "work_packet_authority.py"
        canonical_dependency = dependency.read_bytes()
        dependency.unlink()
        checked = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert checked.returncode != 0
        assert "work_packet_authority.py" in checked.stdout

        dependency.write_bytes(canonical_dependency)
        (target / "tools" / "skills-contract.py").unlink()
        checked = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert checked.returncode != 0
        assert "skills contract" in checked.stdout.lower() or "skills-contract.py" in checked.stdout


def test_skills_compliance_reports_missing_referenced_schema() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-skills-schema-compliance"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/skills-schema-compliance\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        (target / "schemas" / "skills-contract.schema.json").unlink()
        checked = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert checked.returncode != 0
        assert (
            "skills-contract.schema.json" in checked.stdout
            or "skills contract" in checked.stdout.lower()
        )


def test_byte_identical_skills_contract_preserved_on_upgrade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-skills-preserve"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/skills-preserve\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        assert_installed_skills_contract(target)
        before_tool = (target / "tools" / "skills-contract.py").read_bytes()
        before_schema = (target / "schemas" / "skills-contract.schema.json").read_bytes()
        project_path = target / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "pin older version with canonical skills contract")
        upgraded = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        assert "SKILLS_CONTRACT_INSTALLED=<none>" in upgraded.stdout
        assert (target / "tools" / "skills-contract.py").read_bytes() == before_tool
        assert (target / "schemas" / "skills-contract.schema.json").read_bytes() == before_schema
        assert not (target / ".engineering" / "skills.yaml").exists()


def test_custom_skills_schema_fails_closed_before_upgrade_mutation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-custom-skills-schema"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/custom-skills-schema\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        schema = target / "schemas" / "skills-contract.schema.json"
        before_schema = schema.read_bytes()
        before_tool = (target / "tools" / "skills-contract.py").read_bytes()
        schema.write_text('{"title":"custom-skills-schema"}\n', encoding="utf-8")
        project_path = target / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "custom skills schema")
        failed = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert failed.returncode != 0
        assert "skills-contract.schema.json contains local/custom changes" in failed.stdout
        assert schema.read_bytes() == b'{"title":"custom-skills-schema"}\n'
        assert (target / "tools" / "skills-contract.py").read_bytes() == before_tool
        assert not (target / ".engineering" / "skills.yaml").exists()
        del before_schema


def test_fresh_adoption_installs_verification_t4_dependency() -> None:
    required = (
        "tools/verification-contract.py",
        "tools/independent_verifier.py",
        "schemas/verification-contract.schema.json",
        "schemas/trust-evidence-receipt.schema.json",
        "schemas/trust-evidence-boundary.schema.json",
    )
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-verification"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/verification\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout
        for rel in required:
            assert (target / rel).read_text(encoding="utf-8") == (ROOT / rel).read_text(encoding="utf-8"), rel
        assert not (target / ".engineering" / "verification.yaml").exists()
        assert not (target / "tools" / "test_verification_contract.py").exists()
        assert not (target / "tools" / "test_independent_verifier.py").exists()
        checked = run(sys.executable, str(CHECK), "--root", str(target))
        assert checked.returncode == 0, checked.stdout
        (target / "tools" / "independent_verifier.py").unlink()
        missing = run(sys.executable, str(CHECK), "--root", str(target), check=False)
        assert missing.returncode != 0
        assert "independent_verifier.py" in missing.stdout
        assert not (target / ".engineering" / "verification.yaml").exists()

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-verification-divergent"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/verification-divergent\n\ngo 1.23\n", encoding="utf-8")
        (target / "tools").mkdir()
        (target / "tools" / "independent_verifier.py").write_text("print('custom')\n", encoding="utf-8")
        commit_all(target)
        failed = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            check=False,
        )
        assert failed.returncode != 0
        assert "tools/independent_verifier.py contains local/custom changes" in failed.stdout
        assert not (target / ".engineering" / "verification.yaml").exists()
        assert not (target / "tools" / "verification-contract.py").exists()
        assert (target / "tools" / "independent_verifier.py").read_text(encoding="utf-8") == "print('custom')\n"

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-verification-upgrade"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/verification-upgrade\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        custom = "#!/usr/bin/env python3\nprint('custom-verifier')\n"
        (target / "tools" / "independent_verifier.py").write_text(custom, encoding="utf-8")
        project_path = target / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "custom independent verifier")
        before_tool = (target / "tools" / "independent_verifier.py").read_bytes()
        failed = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert failed.returncode != 0
        assert "tools/independent_verifier.py contains local/custom changes" in failed.stdout
        assert (target / "tools" / "independent_verifier.py").read_bytes() == before_tool
        assert not (target / ".engineering" / "verification.yaml").exists()


def test_context_epoch_helper_adoption_and_upgrade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-context-epoch"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/context-epoch\n\ngo 1.23\n",
            encoding="utf-8",
        )
        commit_all(target)
        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout
        helper = target / "tools" / "context_epoch.py"
        assert helper.read_bytes() == (ROOT / "tools" / "context_epoch.py").read_bytes()
        resume = (target / ".cursor" / "commands" / "resume.md").read_text(encoding="utf-8")
        assert "adoption-managed canonical helper" in resume

        helper.unlink()
        compliance = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert compliance.returncode != 0
        assert "references context-epoch helper but missing tools/context_epoch.py" in compliance.stdout

        project_path = target / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "drop context helper before upgrade")

        upgraded = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        assert "CONTEXT_EPOCH_INSTALLED=tools/context_epoch.py" in upgraded.stdout
        assert helper.read_bytes() == (ROOT / "tools" / "context_epoch.py").read_bytes()
        checked = run(sys.executable, str(CHECK), "--root", str(target))
        assert "ENGINEERING_SYSTEM_ADOPTION=PASS" in checked.stdout
        helper.write_text("#!/usr/bin/env python3\nprint('divergent')\n", encoding="utf-8")
        divergent = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert divergent.returncode != 0
        assert "tools/context_epoch.py differs from canonical managed helper" in divergent.stdout

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-custom-context-epoch"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/custom-context\n\ngo 1.23\n",
            encoding="utf-8",
        )
        helper = target / "tools" / "context_epoch.py"
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/usr/bin/env python3\nprint('custom')\n", encoding="utf-8")
        commit_all(target)
        failed = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            check=False,
        )
        assert failed.returncode != 0
        assert "tools/context_epoch.py contains local/custom changes" in failed.stdout
        assert "ADOPTION_BOOTSTRAP=PASS" not in failed.stdout
        assert not (target / "AGENTS.md").exists()


def test_engineering_context_helper_adoption_and_upgrade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-engineering-context"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/engineering-context\n\ngo 1.23\n",
            encoding="utf-8",
        )
        commit_all(target)
        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout
        helper = target / "tools" / "engineering-context.py"
        assert helper.read_bytes() == (ROOT / "tools" / "engineering-context.py").read_bytes()
        resume = (target / ".cursor" / "commands" / "resume.md").read_text(encoding="utf-8")
        assert "tools/engineering-context.py" in resume

        helper.unlink()
        compliance = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert compliance.returncode != 0
        assert "references engineering-context helper but missing tools/engineering-context.py" in compliance.stdout

        project_path = target / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "drop engineering context helper before upgrade")

        upgraded = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        assert "ENGINEERING_CONTEXT_INSTALLED=tools/engineering-context.py" in upgraded.stdout
        assert helper.read_bytes() == (ROOT / "tools" / "engineering-context.py").read_bytes()
        checked = run(sys.executable, str(CHECK), "--root", str(target))
        assert "ENGINEERING_SYSTEM_ADOPTION=PASS" in checked.stdout

        helper.unlink()
        commit_all(target, "remove current-baseline engineering context helper")
        repaired = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "ENGINEERING_CONTEXT_REPAIR=REQUIRED" in repaired.stdout
        assert "ENGINEERING_CONTEXT_INSTALLED=tools/engineering-context.py" in repaired.stdout
        assert "ADOPTION_UPGRADE=PASS" in repaired.stdout
        assert "ADOPTION_UPGRADE=NO_CHANGE" not in repaired.stdout
        assert helper.read_bytes() == (ROOT / "tools" / "engineering-context.py").read_bytes()

        helper.write_text("#!/usr/bin/env python3\nprint('divergent')\n", encoding="utf-8")
        divergent = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert divergent.returncode != 0
        assert "tools/engineering-context.py differs from canonical managed helper" in divergent.stdout

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-custom-engineering-context"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/custom-engineering-context\n\ngo 1.23\n",
            encoding="utf-8",
        )
        helper = target / "tools" / "engineering-context.py"
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/usr/bin/env python3\nprint('custom')\n", encoding="utf-8")
        commit_all(target)
        failed = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            check=False,
        )
        assert failed.returncode != 0
        assert "tools/engineering-context.py contains local/custom changes" in failed.stdout
        assert "ADOPTION_BOOTSTRAP=PASS" not in failed.stdout
        assert not (target / "AGENTS.md").exists()


def test_adoption_compliance_workflow_checks_engineering_context_helper() -> None:
    workflow = (ROOT / ".github" / "workflows" / "adoption-compliance.yml").read_text(
        encoding="utf-8"
    )
    required = (
        "tools/engineering-context.py",
        ".engineering-system-runtime/tools/engineering-context.py",
        "references engineering-context helper but missing tools/engineering-context.py",
        "tools/engineering-context.py differs from canonical managed helper",
        "tools/implementation_preflight.py",
        "tools/work_packet_authority.py",
        'if "tools/implementation_preflight.py" in text:',
        "AGENTS.md references implementation preflight but missing {rel}",
        "canonical compliance runtime missing {rel}",
        "{rel} differs from canonical managed helper",
    )
    for token in required:
        assert token in workflow, token


def test_release_execution_context_is_bounded_and_upgradeable() -> None:
    reusable = (ROOT / ".github" / "workflows" / "release-contract.yml").read_text(
        encoding="utf-8"
    )
    required = (
        "authorize:\n    runs-on: ubuntu-latest",
        'if os.environ.get("CALLER_EVENT_NAME") != "workflow_dispatch"',
        'CALLER_REF_PROTECTED: ${{ github.ref_protected }}',
        "protected-production requires a protected default-branch ref",
        "protected-production candidate must be in default-branch history",
        'ENGINEERING_RELEASE_CONTEXT:-',
        'profile_rel.as_posix() != ".engineering/release.yaml"',
        "release-contract-hosted:",
        "release-contract-protected:",
        'working-directory: ${{ runner.temp }}',
        "python -I - <<'PY'",
        "release profile path must stay inside candidate root",
        "group: engineering-release-production",
        "- self-hosted",
        "- engineering-release-production",
        "ref: ${{ inputs.expected_sha }}",
    )
    for token in required:
        assert token in reusable, token
    assert "runs-on: ${{ inputs." not in reusable
    assert "runs-on: ${{ github.event.inputs" not in reusable
    assert "runner_json" not in reusable

    workflow = yaml.safe_load(reusable)
    assert workflow["jobs"]["release-contract-hosted"]["runs-on"] == "ubuntu-latest"
    protected_runs_on = workflow["jobs"]["release-contract-protected"]["runs-on"]
    assert protected_runs_on["group"] == "engineering-release-production"
    assert protected_runs_on["labels"] == [
        "self-hosted",
        "engineering-release-production",
    ]
    authorize_steps = workflow["jobs"]["authorize"]["steps"]
    hosted_steps = workflow["jobs"]["release-contract-hosted"]["steps"]
    protected_steps = workflow["jobs"]["release-contract-protected"]["steps"]

    authorize_setup_at = next(
        index
        for index, step in enumerate(authorize_steps)
        if "actions/setup-python@" in str(step.get("uses", ""))
    )
    authorize_install_at = next(
        index
        for index, step in enumerate(authorize_steps)
        if step.get("name") == "Install authorization dependency"
    )
    authorize_checkout_at = next(
        index
        for index, step in enumerate(authorize_steps)
        if "actions/checkout@" in str(step.get("uses", ""))
    )
    authorize_run_at = next(
        index
        for index, step in enumerate(authorize_steps)
        if step.get("name") == "Authorize release execution context"
    )
    assert authorize_setup_at < authorize_install_at < authorize_checkout_at < authorize_run_at
    assert authorize_steps[authorize_install_at]["working-directory"] == "${{ runner.temp }}"
    assert "python -I -m pip install" in authorize_steps[authorize_install_at]["run"]
    assert authorize_steps[authorize_checkout_at]["with"]["path"] == "candidate"
    assert authorize_steps[authorize_run_at]["working-directory"] == "${{ runner.temp }}"
    assert "python -I -" in authorize_steps[authorize_run_at]["run"]
    hosted_execute = next(
        step for step in hosted_steps if step.get("name") == "Execute release contract"
    )
    protected_execute = next(
        step for step in protected_steps if step.get("name") == "Execute release contract"
    )
    assert hosted_execute["run"] == protected_execute["run"]
    for steps in (hosted_steps, protected_steps):
        setup_at = next(
            index
            for index, step in enumerate(steps)
            if "actions/setup-python@" in str(step.get("uses", ""))
        )
        install_at = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Install release contract dependency"
        )
        checkout_at = next(
            index for index, step in enumerate(steps) if "actions/checkout@" in str(step.get("uses", ""))
        )
        execute_at = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Execute release contract"
        )
        assert setup_at < install_at < checkout_at < execute_at
        assert steps[install_at]["working-directory"] == "${{ runner.temp }}"
        assert "python -I -m pip install" in steps[install_at]["run"]
        assert steps[checkout_at]["with"]["path"] == "candidate"
        assert steps[execute_at]["working-directory"] == "${{ runner.temp }}"
        assert "python -I -" in steps[execute_at]["run"]
    release_standard = (ROOT / "standards" / "RELEASE.md").read_text(encoding="utf-8")
    assert "repository access to **Selected repositories**" in release_standard
    assert "workflow access to **Selected workflows**" in release_standard
    assert "only explicitly approved production caller repositories" in release_standard
    assert "release-contract.yml@<baseline-sha>" in release_standard
    assert "dedicated `candidate/` subdirectory" in release_standard
    assert "isolated Python import mode" in release_standard
    authorize_step = next(
        step
        for step in workflow["jobs"]["authorize"]["steps"]
        if step.get("name") == "Authorize release execution context"
    )
    match = re.search(r"python(?: -I)? - <<'PY'\n(.*?)\n\s*PY\s*$", authorize_step["run"], re.S)
    assert match is not None
    authorization_code = match.group(1)

    with tempfile.TemporaryDirectory() as auth_tmp:
        auth_root = Path(auth_tmp) / "candidate"
        auth_root.mkdir()
        run("git", "init", "-q", "-b", "main", str(auth_root))
        run("git", "config", "user.email", "test@example.invalid", cwd=auth_root)
        run("git", "config", "user.name", "Release Auth Test", cwd=auth_root)
        (auth_root / ".engineering").mkdir()
        (auth_root / ".engineering/release.yaml").write_text(
            "version: 1\nexact_head_required: true\nexecution_context: protected-production\n",
            encoding="utf-8",
        )
        (auth_root / "yaml.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "Path(os.environ['MALICIOUS_IMPORT_MARKER']).write_text('imported', encoding='utf-8')\n"
            "def safe_load(_value):\n"
            "    return {'execution_context': 'github-hosted'}\n",
            encoding="utf-8",
        )
        commit_all(auth_root)
        remote = Path(auth_tmp) / "origin.git"
        run("git", "clone", "-q", "--bare", str(auth_root), str(remote))
        run("git", "remote", "add", "origin", str(remote), cwd=auth_root)
        candidate = run("git", "rev-parse", "HEAD", cwd=auth_root).stdout.strip()
        output = Path(auth_tmp) / "github-output"
        malicious_import_marker = Path(auth_tmp) / "malicious-imported"
        runner_temp = Path(auth_tmp) / "runner-temp"
        runner_temp.mkdir()

        def authorize(**overrides: str) -> subprocess.CompletedProcess[str]:
            import os
            env = {
                "EXPECTED_SHA": candidate,
                "RELEASE_PROFILE": ".engineering/release.yaml",
                "CANDIDATE_ROOT": str(auth_root),
                "CALLER_EVENT_NAME": "workflow_dispatch",
                "CALLER_REF": "refs/heads/main",
                "CALLER_REF_PROTECTED": "true",
                "DEFAULT_BRANCH": "main",
                "GITHUB_OUTPUT": str(output),
                "MALICIOUS_IMPORT_MARKER": str(malicious_import_marker),
            }
            env.update(overrides)
            return subprocess.run(
                [sys.executable, "-I", "-c", authorization_code],
                cwd=runner_temp,
                env={**os.environ, **env},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

        release_profile = auth_root / ".engineering" / "release.yaml"
        for explicit_invalid in ("false", "null", "0", '""', '" github-hosted "'):
            release_profile.write_text(
                "version: 1\n"
                "exact_head_required: true\n"
                f"execution_context: {explicit_invalid}\n",
                encoding="utf-8",
            )
            invalid_context = authorize()
            assert invalid_context.returncode != 0, explicit_invalid
            assert "unsupported release execution_context" in invalid_context.stdout
        release_profile.write_text(
            "version: 1\nexact_head_required: true\nexecution_context: protected-production\n",
            encoding="utf-8",
        )

        path_escape = authorize(RELEASE_PROFILE="../escape.yaml")
        assert path_escape.returncode != 0
        assert "release profile path must stay inside candidate root" in path_escape.stdout

        absolute_escape = authorize(RELEASE_PROFILE=str(Path(auth_tmp) / "outside.yaml"))
        assert absolute_escape.returncode != 0
        assert "release profile path must stay inside candidate root" in absolute_escape.stdout

        unprotected = authorize(CALLER_REF_PROTECTED="false")
        assert unprotected.returncode != 0
        assert "requires a protected default-branch ref" in unprotected.stdout

        pr_event = authorize(CALLER_EVENT_NAME="pull_request")
        assert pr_event.returncode != 0
        assert "requires workflow_dispatch" in pr_event.stdout

        wrong_ref = authorize(CALLER_REF="refs/heads/feature")
        assert wrong_ref.returncode != 0
        assert "must be dispatched from the default branch" in wrong_ref.stdout

        not_in_history = authorize(EXPECTED_SHA="f" * 40)
        assert not_in_history.returncode != 0
        assert "candidate must be in default-branch history" in not_in_history.stdout

        if output.exists():
            output.unlink()
        allowed = authorize()
        assert allowed.returncode == 0, allowed.stdout
        assert not malicious_import_marker.exists()
        emitted = output.read_text(encoding="utf-8")
        assert "execution_context=protected-production" in emitted
        assert "runner_json" not in emitted

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-protected-release"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/protected-release\n\ngo 1.23\n",
            encoding="utf-8",
        )
        (target / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (target / "RUNBOOK.md").write_text("# Operations Runbook\n", encoding="utf-8")
        commit_all(target)
        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            "--operations-mode",
            "production",
            "--runbook-path",
            "RUNBOOK.md",
            "--health-command",
            "true",
            "--operational-e2e-command",
            "true",
            "--public-smoke-command",
            "true",
            "--full-e2e-passes",
            "1",
            "--release-execution-context",
            "protected-production",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout
        release_path = target / ".engineering" / "release.yaml"
        release = load_yaml(release_path)
        assert release["execution_context"] == "protected-production"
        caller = (target / ".github/workflows/engineering-release.yml").read_text(
            encoding="utf-8"
        )
        assert "engineering-release-production" not in caller
        assert "runner_json" not in caller

        release["execution_context"] = "attacker-controlled"
        release_path.write_text(yaml.safe_dump(release, sort_keys=False), encoding="utf-8")
        rejected = run(
            sys.executable, str(CHECK), "--root", str(target), check=False
        )
        assert rejected.returncode != 0
        assert "release.yaml execution_context is unsupported" in rejected.stdout
        commit_all(target, "prepare invalid same-version release context")

        no_change_invalid = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--audit",
            "--baseline-sha",
            BASELINE,
            check=False,
        )
        assert no_change_invalid.returncode != 0
        assert "release execution_context is unsupported" in no_change_invalid.stdout, no_change_invalid.stdout
        assert "ADOPTION_UPGRADE=NO_CHANGE" not in no_change_invalid.stdout
        assert "ADOPTION_UPGRADE_AUDIT=PASS" not in no_change_invalid.stdout

        release["execution_context"] = False
        release_path.write_text(yaml.safe_dump(release, sort_keys=False), encoding="utf-8")
        commit_all(target, "prepare falsey same-version release context")
        falsey_no_change = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--audit",
            "--baseline-sha",
            BASELINE,
            check=False,
        )
        assert falsey_no_change.returncode != 0
        assert "release execution_context is unsupported" in falsey_no_change.stdout
        assert "ADOPTION_UPGRADE=NO_CHANGE" not in falsey_no_change.stdout
        assert "ADOPTION_UPGRADE_AUDIT=PASS" not in falsey_no_change.stdout

        project_path = target / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "prepare invalid inherited release context")
        invalid_upgrade = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--audit",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert invalid_upgrade.returncode != 0
        assert "release execution_context is unsupported" in invalid_upgrade.stdout
        assert run("git", "status", "--porcelain", cwd=target).stdout == ""

        release["execution_context"] = "protected-production"
        release_path.write_text(yaml.safe_dump(release, sort_keys=False), encoding="utf-8")
        commit_all(target, "prepare protected release upgrade")
        upgraded = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        upgraded_release = load_yaml(release_path)
        assert upgraded_release["execution_context"] == "protected-production"


def test_bun_native_discovery() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-bun"
        target.mkdir()
        init_repo(target)
        (target / "package.json").write_text(
            '{"scripts":{"test":"vitest run","build":"vite build","lint":"eslint .","typecheck":"tsc --noEmit"}}\n',
            encoding="utf-8",
        )
        (target / "bun.lockb").write_bytes(b"bun")
        (target / "src").mkdir()
        commit_all(target)

        audit = run(sys.executable, str(ADOPT), "--root", str(target), "--audit")
        assert '"build": "bun run build"' in audit.stdout
        assert '"lint": "bun run lint"' in audit.stdout
        assert '"typecheck": "bun run typecheck"' in audit.stdout
        assert "bun test" in audit.stdout


def main() -> int:
    run(sys.executable, "-m", "py_compile", str(ADOPT), str(CHECK), str(UPGRADE))
    test_clean_python_bootstrap()
    test_rule_review_is_fail_closed()
    test_existing_ci_requires_mapping_when_ambiguous()
    test_operations_signals_fail_closed_then_production_profile()
    test_quality_and_domain_discovery()
    test_managed_upgrade_to_1_6()
    test_same_version_1_6_5_pre_context_epoch_upgrade()
    test_same_version_1_6_5_context_epoch_resume_upgrade()
    test_managed_file_hash_manifests_match_immutable_revisions()
    test_managed_file_hash_history_upgrades_known_bytes_and_rejects_custom()
    test_custom_cursorignore_preserved_on_upgrade()
    test_supported_managed_cursor_rule_history_is_upgradeable()
    test_custom_cursor_rule_fails_closed_before_upgrade_mutation()
    test_custom_resume_adapter_fails_closed()
    test_grant_style_baseline_declarations_upgraded()
    test_ambiguous_baseline_declaration_fails_closed()
    test_stale_outside_form_declaration_fails_without_partial_upgrade()
    test_optional_knowledge_contract_adoption_and_upgrade()
    test_preexisting_custom_knowledge_contract_fails_closed_before_bootstrap()
    test_runtime_contract_preserves_existing_authority_commands()
    test_optional_skills_contract_adoption_and_upgrade()
    test_preexisting_custom_skills_contract_fails_closed_before_bootstrap()
    test_skills_compliance_reports_missing_referenced_helper()
    test_skills_compliance_reports_missing_referenced_schema()
    test_byte_identical_skills_contract_preserved_on_upgrade()
    test_custom_skills_schema_fails_closed_before_upgrade_mutation()
    test_fresh_adoption_installs_verification_t4_dependency()
    test_context_epoch_helper_adoption_and_upgrade()
    test_engineering_context_helper_adoption_and_upgrade()
    test_adoption_compliance_workflow_checks_engineering_context_helper()
    test_release_execution_context_is_bounded_and_upgradeable()
    test_bun_native_discovery()
    print("ADOPTION_TOOL_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
