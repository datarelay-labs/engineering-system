#!/usr/bin/env python3
"""Regression tests for deterministic Engineering System adoption bootstrap."""
from __future__ import annotations

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
            ".cursor/commands/resume.md",
            ".github/ISSUE_TEMPLATE/ai-work-packet.md",
            ".github/workflows/engineering-system.yml",
            ".github/workflows/engineering-release.yml",
        )
        for rel in required:
            assert (target / rel).is_file(), rel

        project = load_yaml(target / ".engineering/project.yaml")
        engineering = project["engineering_system"]
        assert engineering["version"] == "1.6.1"
        assert engineering["mode"] == "adopted"
        assert engineering["ci_mode"] == "shared"
        assert engineering["baseline"] == BASELINE
        assert engineering["native_ci_workflows"] == []
        assert engineering["merge_gate_status"] == "unknown"
        assert project["operations"]["production_oriented"] is False
        assert project["operations"]["incident_response_required"] is False

        tests_text = (target / ".engineering/tests.yaml").read_text(encoding="utf-8")
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

        upgraded_project = load_yaml(project_path)
        assert upgraded_project["engineering_system"]["version"] == "1.6.1"
        assert upgraded_project["engineering_system"]["baseline"] == NEW_BASELINE
        upgraded_workflow = workflow_path.read_text(encoding="utf-8")
        assert f"adoption-compliance.yml@{NEW_BASELINE}" in upgraded_workflow
        assert f"enforcement-check.yml@{NEW_BASELINE}" in upgraded_workflow
        assert f"affected-tests.yml@{NEW_BASELINE}" in upgraded_workflow


def main() -> int:
    run(sys.executable, "-m", "py_compile", str(ADOPT), str(CHECK), str(UPGRADE))
    test_clean_python_bootstrap()
    test_rule_review_is_fail_closed()
    test_existing_ci_requires_mapping_when_ambiguous()
    test_operations_signals_fail_closed_then_production_profile()
    test_quality_and_domain_discovery()
    test_managed_upgrade_to_1_6()
    print("ADOPTION_TOOL_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
