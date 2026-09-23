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
    test_bun_native_discovery()
    print("ADOPTION_TOOL_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
