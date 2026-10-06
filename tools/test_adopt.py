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

from adopt import GOVERNANCE_EPOCH, POLICY_EPOCH, REQUIRED_MANAGED, rewrite_legacy_coordination_rules

ROOT = Path(__file__).resolve().parents[1]
ADOPT = ROOT / "tools" / "adopt.py"
CHECK = ROOT / "tools" / "check-adoption.py"
UPGRADE = ROOT / "tools" / "upgrade-adoption.py"
BASELINE = "a" * 40
NEW_BASELINE = "b" * 40
CONTEXT_EPOCH_BASELINE = "cdc54b3220b5ec38e84dc2c33bd500b35edd6b39"
TRUST_HELPER_BASELINE = "dfe9b2c5ad47cc2e4ef6563717a7722635251fe9"
PROVIDER_NEUTRAL_BASELINE = "6bf89e1fc716eff242a10eeb84dd25b5186ccc71"
STAGE_A_BASELINE = "04ea5080440371e895d9a57570ba70e6a3318781"
PROFILE_V2_BASELINE = "298ea8bd937cc1d5c84837ec30e6cd24fb8d93ce"


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
        (target / ".cursor").mkdir()
        (target / ".cursor" / "legacy.txt").write_text("legacy\n", encoding="utf-8")
        (target / ".cursorignore").write_text("legacy ignore\n", encoding="utf-8")
        (target / ".cursorrules").write_text("legacy rules\n", encoding="utf-8")
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
        assert "RETIRED_AGENT_ARTIFACTS_REMOVED=.cursor,.cursorignore,.cursorrules" in applied.stdout
        for rel in (".cursor", ".cursorignore", ".cursorrules"):
            assert not (target / rel).exists(), rel
        assert "OPERATIONS_MODE=nonproduction" in applied.stdout
        assert "MERGE_GATE_ENFORCEMENT=unknown" in applied.stdout

        required = (
            "AGENTS.md",
            ".engineering/project.yaml",
            ".engineering/tests.yaml",
            ".engineering/release.yaml",
            ".github/ISSUE_TEMPLATE/ai-work-packet.md",
            ".github/workflows/engineering-system.yml",
            ".github/workflows/engineering-release.yml",
            "tools/implementation_preflight.py",
            "tools/terminal_completion_notify.py",
            "tools/governance_floor.py",
        )
        for rel in required:
            assert (target / rel).is_file(), rel

        project = load_yaml(target / ".engineering/project.yaml")
        engineering = project["engineering_system"]
        assert engineering["version"] == "1.7.0"
        assert engineering["policy_epoch"] == POLICY_EPOCH
        assert engineering["governance_epoch"] == GOVERNANCE_EPOCH
        assert engineering["mode"] == "adopted"
        assert engineering["ci_mode"] == "shared"
        assert engineering["baseline"] == BASELINE
        assert engineering["native_ci_workflows"] == []
        assert engineering["merge_gate_status"] == "unknown"
        assert project["operations"]["production_oriented"] is False
        assert project["operations"]["incident_response_required"] is False

        tests_text = (target / ".engineering/tests.yaml").read_text(encoding="utf-8")
        assert "cost: medium" in tests_text
        assert "agent_default: true" in tests_text
        assert "ENGINEERING_BASE_REF" in tests_text
        assert "origin/main...HEAD" in tests_text
        assert tests_text.count('command: "git diff --check"') == 0
        assert 'setup_command: "python -m pip install -e . && python -m pip install pytest"' in tests_text

        workflow_text = (target / ".github/workflows/engineering-system.yml").read_text(encoding="utf-8")
        assert f"governance-floor.yml@{BASELINE}" in workflow_text
        assert "pull_request_target" in workflow_text
        assert f"governance-floor.yml@{BASELINE}" in workflow_text
        assert "pull_request_target" in workflow_text
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
            sys.executable, str(ADOPT), "--root", str(target), "--apply",
            "--baseline-sha", BASELINE, "--test-command", "go test ./...",
        )
        for rel in (".cursor", ".cursorignore", ".cursorrules"):
            assert not (target / rel).exists(), rel

        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.5.0"
        project["engineering_system"]["baseline"] = BASELINE
        project["engineering_system"]["policy_epoch"] = 3
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")

        workflow_path = target / ".github/workflows/engineering-system.yml"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        enforcement_block = (
            "\n  enforcement-reconcile:\n"
            f"    uses: datarelay-labs/engineering-system/.github/workflows/enforcement-check.yml@{BASELINE}\n"
        )
        workflow_path.write_text(workflow_text.replace(enforcement_block, ""), encoding="utf-8")

        agents_path = target / "AGENTS.md"
        agents_text = agents_path.read_text(encoding="utf-8")
        profile_line = next(
            line for line in agents_text.splitlines()
            if line.startswith("- **Execution profile authority:**")
        )
        policy_line = next(
            line for line in agents_text.splitlines()
            if line.startswith("- **Execute useful work continuously.**")
        )
        next_chat_bootstrap_line = next(
            line for line in agents_text.splitlines()
            if line.startswith("- **Next-chat bootstrap fast path:**")
        )
        verified_next_chat_resume_line = next(
            line for line in agents_text.splitlines()
            if line.startswith("- **Verified next-chat resume:**")
        )
        external_write_line = next(
            line for line in agents_text.splitlines()
            if line.startswith("- For ordinary authenticated GitHub Issue/PR coordination,")
        )
        legacy_profile = (
            "- ChatGPT Chat is the implementation path. Treat the Work Packet as durable coordination state, "
            "verify the target repository/branch/HEAD before mutation, and use ordinary authenticated Git/GitHub "
            "operations for normal repository work."
        )
        legacy_external_write = (
            "- Before an external Issue/PR write, run `python3 tools/worker_adapter.py evaluate --request-json "
            "<facts.json>` against a fresh authoritative read. Proceed only on `APPLIED`. A revision or head "
            "mismatch is `STALE_WORKER` and must not write."
        )
        agents_text = agents_text.replace(profile_line, legacy_profile, 1)
        agents_text = agents_text.replace(external_write_line, legacy_external_write, 1)
        agents_text = agents_text.replace(policy_line + "\n", "", 1)
        agents_text = agents_text.replace(next_chat_bootstrap_line + "\n", "", 1)
        agents_text = agents_text.replace(verified_next_chat_resume_line + "\n", "", 1)
        agents_text = (
            f"Adoption baseline: Engineering System version 1.5.0 at immutable commit `{BASELINE}`.\n\n"
            + agents_text
            + "\nChatGPT Chat is the default implementer for this repository when the authenticated active Work Packet authorizes the exact repository/worktree/branch/scope. Cursor is disabled by default and must not be started, resumed, or waited on unless the owner explicitly reactivates it for the current Work Packet with `IMPLEMENTER=CURSOR`.\n"
            + "15. Cursor adapter is disabled by default and must not be started, resumed, attached to, waited on, or used for implementation unless the owner explicitly reactivates it for the current Work Packet and records `IMPLEMENTER=CURSOR`. Cursor quota/session state must never block normal Atlas development.\n"
            + "\n## Product-specific invariant\n\n- preserve-project-rule\n"
        )
        agents_path.write_text(agents_text, encoding="utf-8")

        (target / ".cursor/rules").mkdir(parents=True)
        (target / ".cursor/rules/project-custom.mdc").write_text("custom legacy adapter\n", encoding="utf-8")
        (target / ".cursorignore").write_text("custom legacy ignore\n", encoding="utf-8")
        (target / ".cursorrules").write_text("custom legacy rules\n", encoding="utf-8")
        commit_all(target, "legacy adapter and stale policy fixture")

        upgraded = run(
            sys.executable, str(UPGRADE), "--root", str(target), "--apply",
            "--baseline-sha", NEW_BASELINE,
        )
        assert "ADOPTION_UPGRADE=PASS" in upgraded.stdout
        assert "EXECUTION_POLICY_SYNCED=YES" in upgraded.stdout
        assert "RETIRED_AGENT_ARTIFACTS_REMOVED=.cursor,.cursorignore,.cursorrules" in upgraded.stdout
        for rel in (".cursor", ".cursorignore", ".cursorrules"):
            assert not (target / rel).exists(), rel
        upgraded_agents = agents_path.read_text(encoding="utf-8")
        assert upgraded_agents.count("- **Execute useful work continuously.**") == 1
        assert upgraded_agents.count("- **Product execution ownership / supervisor fallback:**") == 1
        assert upgraded_agents.count("- **Next-chat bootstrap fast path:**") == 1
        assert upgraded_agents.count("- **Verified next-chat resume:**") == 1
        assert "NO_ACTIVE_PACKET` is a scheduling input, not a blocker" in upgraded_agents
        assert "enter the persisted Next Action immediately" in upgraded_agents
        assert "uncontrolled Issue proliferation" in upgraded_agents
        assert "Never mutate an actively progressing owner-authorized worker dirty worktree" in upgraded_agents
        assert "During machine-observable waits, preserve state and advance independent work" in upgraded_agents
        assert "make measurable progress in the same turn" in upgraded_agents.lower()
        assert "After a bounded packet/PR/test phase completes, immediately return to roadmap scheduling" in upgraded_agents
        assert "roadmap/release objective is complete" in upgraded_agents
        assert "repair missing/stale/contradictory packet state from repository evidence" in upgraded_agents
        assert "Implement, test and audit directly in coherent batches" in upgraded_agents
        assert "never create one Issue per finding" in upgraded_agents
        assert "Close COMPLETE packets in the same lifecycle reconciliation" in upgraded_agents
        assert "- preserve-project-rule" in upgraded_agents
        assert ".engineering/execution-profile.yaml" in upgraded_agents
        assert "`.engineering/execution-profile.yaml` selects the runtime" in upgraded_agents
        assert "additional magic phrase" in upgraded_agents
        assert "ordinary authenticated GitHub Issue/PR coordination" in upgraded_agents
        assert "Stronger trusted boundaries apply only to effect classes" in upgraded_agents
        assert "Cursor" not in upgraded_agents
        assert "Before an external Issue/PR write, run `python3 tools/worker_adapter.py" not in upgraded_agents
        assert "IMPLEMENTER=CURSOR" not in upgraded_agents
        assert "owner explicitly reactivates" not in upgraded_agents
        assert "Cursor adapter is disabled by default" not in upgraded_agents
        assert f"Engineering System version 1.7.0 at immutable commit `{NEW_BASELINE}`" in upgraded_agents
        assert BASELINE not in upgraded_agents

        upgraded_project = load_yaml(project_path)
        assert upgraded_project["engineering_system"]["version"] == "1.7.0"
        assert upgraded_project["engineering_system"]["policy_epoch"] == POLICY_EPOCH
        assert upgraded_project["engineering_system"]["baseline"] == NEW_BASELINE
        upgraded_workflow = workflow_path.read_text(encoding="utf-8")
        assert f"governance-floor.yml@{NEW_BASELINE}" in upgraded_workflow
        assert "pull_request_target" in upgraded_workflow
        assert f"adoption-compliance.yml@{NEW_BASELINE}" in upgraded_workflow
        assert f"enforcement-check.yml@{NEW_BASELINE}" in upgraded_workflow
        assert f"affected-tests.yml@{NEW_BASELINE}" in upgraded_workflow




def test_generated_agents_references_only_managed_tools() -> None:
    template = (ROOT / "templates/AGENTS.md").read_text(encoding="utf-8")
    referenced: set[str] = set()
    for line in template.splitlines():
        if "canonical Engineering System checkout" in line:
            continue
        referenced.update(re.findall(r"tools/[A-Za-z0-9_.-]+\.py", line))
    managed = {path for path in REQUIRED_MANAGED if path.startswith("tools/")}
    missing = sorted(referenced - managed)
    assert not missing, missing

def test_general_upgrade_requires_stage_a_bridge_before_profile_v3() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-prebridge-upgrade"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/prebridge-upgrade\n\ngo 1.23\n",
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
        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.5"
        project["engineering_system"]["policy_epoch"] = 2
        project_path.write_text(
            yaml.safe_dump(project, sort_keys=False),
            encoding="utf-8",
        )
        for rel in (
            ".engineering/execution-profile.yaml",
            "tools/execution_profile.py",
            "schemas/execution-profile.schema.json",
        ):
            (target / rel).unlink()
        commit_all(target, "simulate supported pre-bridge adoption")
        before = run("git", "status", "--porcelain", cwd=target).stdout
        blocked = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert blocked.returncode != 0
        assert "requires the Stage-A legacy-v2 execution-profile bridge" in blocked.stdout
        assert run("git", "status", "--porcelain", cwd=target).stdout == before


def test_same_baseline_governance_floor_repair_emits_root_migration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-governance-repair"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/governance-repair\n\ngo 1.23\n",
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
        commit_all(target, "adopt current baseline")

        project_path = target / ".engineering/project.yaml"
        before_project = load_yaml(project_path)
        before_epoch = before_project["engineering_system"]["policy_epoch"]
        before_governance_epoch = before_project["engineering_system"]["governance_epoch"]
        assert before_epoch == POLICY_EPOCH
        assert before_governance_epoch == GOVERNANCE_EPOCH

        (target / "tools/governance_floor.py").unlink()
        commit_all(target, "remove managed governance helper")
        migration_base = run("git", "rev-parse", "HEAD", cwd=target).stdout.strip()

        repaired = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
        )
        assert "GOVERNANCE_FLOOR_REPAIR=REQUIRED" in repaired.stdout
        assert "GOVERNANCE_ROOT_MIGRATION=REQUIRED" in repaired.stdout
        assert "GOVERNANCE_ROOT_MIGRATION_WRITTEN=YES" in repaired.stdout
        assert "ADOPTION_UPGRADE=PASS" in repaired.stdout

        after_project = load_yaml(project_path)
        assert after_project["engineering_system"]["policy_epoch"] == before_epoch
        assert (
            after_project["engineering_system"]["governance_epoch"]
            == before_governance_epoch + 1
        )

        migration_path = target / ".engineering/governance-migration.yaml"
        migration = load_yaml(migration_path)
        assert migration["contract_version"] == 2
        assert migration["base_sha"] == migration_base
        assert migration["from_governance_epoch"] == before_governance_epoch
        assert migration["to_governance_epoch"] == before_governance_epoch + 1
        assert migration["requires_exact_head_validate"] is True
        assert migration["automation_eligible"] is False
        assert [item["path"] for item in migration["changed_surfaces"]] == [
            "tools/governance_floor.py"
        ]
        helper_blob = run(
            "git",
            "hash-object",
            "tools/governance_floor.py",
            cwd=target,
        ).stdout.strip()
        assert migration["changed_surfaces"][0]["head_blob_sha"] == helper_blob


def test_legacy_root_repair_cannot_exceed_canonical_policy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "legacy-root-over-canonical"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/legacy-root-over-canonical\n\ngo 1.23\n",
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
        commit_all(target, "adopt current baseline")

        helper = target / "tools/governance_floor.py"
        helper.write_text(
            helper.read_text(encoding="utf-8").replace(
                "GOVERNANCE_MIGRATION_CONTRACT_VERSION = 2",
                "GOVERNANCE_MIGRATION_CONTRACT_VERSION = 1",
            ),
            encoding="utf-8",
        )
        commit_all(target, "simulate legacy v1 floor")
        helper.unlink()
        commit_all(target, "remove legacy governance floor")

        blocked = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            check=False,
        )
        assert blocked.returncode != 0
        assert (
            "legacy root migration would advance policy_epoch beyond canonical policy"
            in blocked.stdout
        )
        engineering = load_yaml(target / ".engineering/project.yaml")["engineering_system"]
        assert engineering["policy_epoch"] == POLICY_EPOCH
        assert engineering["governance_epoch"] == GOVERNANCE_EPOCH


def test_v2_same_baseline_rejects_newer_policy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "normalize-legacy-policy"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/normalize-legacy-policy\n\ngo 1.23\n",
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
        commit_all(target, "adopt current baseline")

        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["policy_epoch"] = POLICY_EPOCH + 1
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        inflated_base = commit_all(target, "simulate legacy over-canonical policy epoch")
        if inflated_base is None:
            inflated_base = run("git", "rev-parse", "HEAD", cwd=target).stdout.strip()

        rejected = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            check=False,
        )
        assert rejected.returncode != 0
        assert "policy_epoch is newer than canonical policy" in rejected.stdout
        engineering = load_yaml(project_path)["engineering_system"]
        assert engineering["policy_epoch"] == POLICY_EPOCH + 1
        assert engineering["governance_epoch"] == GOVERNANCE_EPOCH


def test_same_baseline_repairs_managed_execution_policy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-policy-repair"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/policy-repair\n\ngo 1.23\n",
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

        agents_path = target / "AGENTS.md"
        agents_text = agents_path.read_text(encoding="utf-8")
        policy_line = next(
            line for line in agents_text.splitlines()
            if line.startswith("- **Execute useful work continuously.**")
        )
        next_chat_bootstrap_line = next(
            line for line in agents_text.splitlines()
            if line.startswith("- **Next-chat bootstrap fast path:**")
        )
        verified_next_chat_resume_line = next(
            line for line in agents_text.splitlines()
            if line.startswith("- **Verified next-chat resume:**")
        )
        agents_text = agents_text.replace(policy_line + "\n", "", 1)
        agents_text = agents_text.replace(next_chat_bootstrap_line + "\n", "", 1)
        agents_text = agents_text.replace(verified_next_chat_resume_line + "\n", "", 1)
        agents_text += "\n## Product-specific invariant\n\n- preserve-same-baseline-rule\n"
        agents_path.write_text(agents_text, encoding="utf-8")
        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["policy_epoch"] = 1
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "simulate managed policy drift")

        failed = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert failed.returncode != 0
        assert "AGENTS.md missing managed continuous-execution policy" in failed.stdout

        repaired = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
        )
        assert "EXECUTION_POLICY_REPAIR=REQUIRED" in repaired.stdout
        assert "EXECUTION_POLICY_SYNCED=YES" in repaired.stdout
        assert f"POLICY_EPOCH_REPAIR={POLICY_EPOCH}" in repaired.stdout
        assert "ADOPTION_UPGRADE=PASS" in repaired.stdout
        repaired_agents = agents_path.read_text(encoding="utf-8")
        assert repaired_agents.count("- **Execute useful work continuously.**") == 1
        assert repaired_agents.count("- **Next-chat bootstrap fast path:**") == 1
        assert repaired_agents.count("- **Verified next-chat resume:**") == 1
        assert "- preserve-same-baseline-rule" in repaired_agents
        repaired_project = load_yaml(project_path)
        assert repaired_project["engineering_system"]["policy_epoch"] == POLICY_EPOCH


def test_managed_upgrade_rejects_unclassified_next_chat_policy_suffixes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-next-chat-suffix"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/next-chat-suffix\n\ngo 1.23\n",
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

        agents_path = target / "AGENTS.md"
        agents_text = agents_path.read_text(encoding="utf-8")
        suffix = " Product-specific approval remains required before this action."
        for marker in (
            "- **Next-chat bootstrap fast path:**",
            "- **Verified next-chat resume:**",
        ):
            line = next(line for line in agents_text.splitlines() if line.startswith(marker))
            agents_text = agents_text.replace(line, line + suffix, 1)
        agents_path.write_text(agents_text, encoding="utf-8")

        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "unclassified next-chat policy suffixes")
        before = agents_path.read_bytes()

        checked = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert checked.returncode != 0
        assert "customized or missing managed next-chat policy requires review" in checked.stdout

        rejected = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert rejected.returncode != 0
        assert "customized managed next-chat-bootstrap policy line" in rejected.stdout
        assert agents_path.read_bytes() == before


def test_managed_upgrade_rejects_ambiguous_next_chat_policy_edit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-next-chat-ambiguous"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/next-chat-ambiguous\n\ngo 1.23\n",
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

        agents_path = target / "AGENTS.md"
        agents_text = agents_path.read_text(encoding="utf-8")
        canonical_line = next(
            line
            for line in agents_text.splitlines()
            if line.startswith("- **Next-chat bootstrap fast path:**")
        )
        customized_line = canonical_line.replace(
            "perform one bounded authoritative Work Packet lookup",
            "perform one owner-approved bounded authoritative Work Packet lookup",
            1,
        )
        assert customized_line != canonical_line
        agents_path.write_text(
            agents_text.replace(canonical_line, customized_line, 1),
            encoding="utf-8",
        )

        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.4"
        project["engineering_system"]["baseline"] = BASELINE
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(target, "ambiguous next-chat policy edit")
        before = agents_path.read_bytes()

        rejected = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert rejected.returncode != 0
        assert "customized managed next-chat-bootstrap policy line" in rejected.stdout
        assert agents_path.read_bytes() == before


def test_unknown_cursor_agent_rule_fails_closed_before_upgrade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "unknown-cursor-rule"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text("module example.invalid/cursor-rule\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)
        run(
            sys.executable, str(ADOPT), "--root", str(target), "--apply",
            "--baseline-sha", BASELINE, "--test-command", "go test ./...",
        )
        agents = target / "AGENTS.md"
        agents.write_text(
            agents.read_text(encoding="utf-8") + "\nCustom Cursor execution rule that is not canonical.\n",
            encoding="utf-8",
        )
        commit_all(target, "custom cursor rule")
        before = agents.read_bytes()
        blocked = run(
            sys.executable, str(UPGRADE), "--root", str(target), "--apply",
            "--baseline-sha", NEW_BASELINE, check=False,
        )
        assert blocked.returncode != 0
        assert "unrecognized retired runtime rules" in blocked.stdout
        assert agents.read_bytes() == before


def test_managed_file_hash_manifests_match_immutable_revisions() -> None:
    """Historical hash manifests must be derived from the named canonical commits."""
    cases = (
        ("1.6.5-cdc54b3.sha256", CONTEXT_EPOCH_BASELINE),
        ("1.6.5-dfe9b2c.sha256", TRUST_HELPER_BASELINE),
        ("1.7.0-6bf89e1.sha256", PROVIDER_NEUTRAL_BASELINE),
        ("1.7.0-04ea508.sha256", STAGE_A_BASELINE),
        ("1.7.0-298ea8b.sha256", PROFILE_V2_BASELINE),
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


def test_same_baseline_repairs_prior_1_7_managed_bytes_after_metadata_stamp() -> None:
    """Interrupted rollout repairs trusted prior canonical bytes after baseline stamp."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-prior-managed-repair"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/prior-managed-repair\n\ngo 1.23\n",
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
        commit_all(target, "adopt target baseline")

        stale_paths = (
            ".github/ISSUE_TEMPLATE/ai-work-packet.md",
            "tools/implementation_preflight.py",
            "tools/context_epoch.py",
        )
        for rel in stale_paths:
            historical = run(
                "git",
                "show",
                f"{PROVIDER_NEUTRAL_BASELINE}:{rel}",
                cwd=ROOT,
            ).stdout
            assert historical != (ROOT / rel).read_text(encoding="utf-8"), rel
            (target / rel).write_text(historical, encoding="utf-8")
        commit_all(target, "simulate stamped baseline with prior managed bytes")
        damaged_base = run("git", "rev-parse", "HEAD", cwd=target).stdout.strip()

        repaired = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
        )
        for marker in (
            "WORK_PACKET_TEMPLATE_REPAIR=REQUIRED",
            "IMPLEMENTATION_PREFLIGHT_REPAIR=REQUIRED",
            "CONTEXT_EPOCH_REPAIR=REQUIRED",
            "GOVERNANCE_ROOT_MIGRATION=REQUIRED",
            "ADOPTION_UPGRADE=PASS",
        ):
            assert marker in repaired.stdout, (marker, repaired.stdout)
        for rel in stale_paths:
            assert (target / rel).read_bytes() == (ROOT / rel).read_bytes(), rel

        commit_all(target, "repair prior managed bytes")
        repaired_head = run("git", "rev-parse", "HEAD", cwd=target).stdout.strip()
        floor_check = run(
            sys.executable,
            str(target / "tools/governance_floor.py"),
            "check",
            "--root",
            str(target),
            "--base-ref",
            damaged_base,
            "--head-ref",
            repaired_head,
            check=False,
        )
        assert floor_check.returncode == 0, floor_check.stdout
        assert "GOVERNANCE_FLOOR=PASS" in floor_check.stdout


def test_same_baseline_repairs_stage_a_managed_bytes_after_metadata_stamp() -> None:
    """Interrupted Stage-B rollout repairs exact Stage-A managed bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-stage-a-repair"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/stage-a-repair\n\ngo 1.23\n",
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
        commit_all(target, "adopt target baseline")

        stale_paths = (
            ".github/ISSUE_TEMPLATE/ai-work-packet.md",
            ".engineering/execution-profile.yaml",
            "tools/context_epoch.py",
        )
        for rel in stale_paths:
            historical_ref = (
                PROFILE_V2_BASELINE
                if rel == ".engineering/execution-profile.yaml"
                else STAGE_A_BASELINE
            )
            historical = run(
                "git",
                "show",
                f"{historical_ref}:{rel}",
                cwd=ROOT,
            ).stdout
            assert historical != (ROOT / rel).read_text(encoding="utf-8"), rel
            (target / rel).write_text(historical, encoding="utf-8")
        commit_all(target, "simulate stamped baseline with Stage-A managed bytes")
        damaged_base = run("git", "rev-parse", "HEAD", cwd=target).stdout.strip()

        repaired = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
        )
        for marker in (
            "WORK_PACKET_TEMPLATE_REPAIR=REQUIRED",
            "EXECUTION_PROFILE_REPAIR=REQUIRED",
            "CONTEXT_EPOCH_REPAIR=REQUIRED",
            "GOVERNANCE_ROOT_MIGRATION=REQUIRED",
            "ADOPTION_UPGRADE=PASS",
        ):
            assert marker in repaired.stdout, (marker, repaired.stdout)
        for rel in stale_paths:
            assert (target / rel).read_bytes() == (ROOT / rel).read_bytes(), rel

        commit_all(target, "repair Stage-A managed bytes")
        repaired_head = run("git", "rev-parse", "HEAD", cwd=target).stdout.strip()
        floor_check = run(
            sys.executable,
            str(target / "tools/governance_floor.py"),
            "check",
            "--root",
            str(target),
            "--base-ref",
            damaged_base,
            "--head-ref",
            repaired_head,
        )
        assert "GOVERNANCE_FLOOR=PASS" in floor_check.stdout


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










def test_managed_file_old_baseline_is_trusted_without_manifest() -> None:
    upgrade_path = ROOT / "tools" / "upgrade-adoption.py"
    spec = importlib.util.spec_from_file_location("upgrade_adoption_old_baseline", upgrade_path)
    assert spec and spec.loader
    upgrade = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upgrade)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        canonical = base / "canonical"
        target = base / "target"
        rel = "tools/example-managed.py"
        canonical.mkdir()
        init_repo(canonical)
        (canonical / rel).parent.mkdir(parents=True, exist_ok=True)
        prior = b"prior baseline managed bytes\n"
        current = b"current managed bytes\n"
        custom = b"project custom bytes\n"
        (canonical / rel).write_bytes(prior)
        commit_all(canonical, "prior baseline")
        prior_sha = run("git", "rev-parse", "HEAD", cwd=canonical).stdout.strip()
        (canonical / rel).write_bytes(current)
        commit_all(canonical, "current baseline")
        (target / rel).parent.mkdir(parents=True, exist_ok=True)

        original = upgrade.CANONICAL
        upgrade.CANONICAL = canonical
        try:
            (target / rel).write_bytes(prior)
            planned = upgrade.plan_managed_file_install(
                target, (rel,), label="fixture", old_baseline=prior_sha
            )
            assert planned == {rel: current.decode("utf-8")}

            (target / rel).write_bytes(custom)
            try:
                upgrade.plan_managed_file_install(
                    target, (rel,), label="fixture", old_baseline=prior_sha
                )
            except SystemExit as exc:
                assert "local/custom changes" in str(exc)
            else:
                raise AssertionError("custom bytes were accepted from old baseline trust")
        finally:
            upgrade.CANONICAL = original


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
            f"Adoption baseline: Engineering System version 1.7.0 at immutable commit `{NEW_BASELINE}`."
            in agents_text
        )
        assert "1.6.1" not in agents_text
        assert stale_sha not in agents_text
        assert (
            "The current repository baseline identifies Engineering System **1.7.0** and keeps"
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
        assert not (target / "tools" / "atlas-context-contract.py").exists()
        assert not (target / "tools" / "atlas-workflow.py").exists()
        managed_rules = (target / "AGENTS.md").read_text(encoding="utf-8")
        for organization_term in ("DataRelay Atlas", "Telegram", "Tela", "Athena"):
            assert organization_term not in managed_rules

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
        affected_selector = target / "tools" / "affected_test_selection.py"
        assert affected_selector.read_bytes() == (
            ROOT / "tools" / "affected_test_selection.py"
        ).read_bytes()
        notifier = target / "tools" / "terminal_completion_notify.py"
        assert notifier.read_bytes() == (
            ROOT / "tools" / "terminal_completion_notify.py"
        ).read_bytes()
        helper.unlink()
        affected_selector.unlink()
        notifier.unlink()
        compliance = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert compliance.returncode != 0
        assert "managed-profile adoption missing required helper tools/context_epoch.py" in compliance.stdout
        assert (
            "managed adoption missing required file: tools/affected_test_selection.py"
            in compliance.stdout
        )
        assert (
            "managed-profile adoption missing required helper tools/terminal_completion_notify.py"
            in compliance.stdout
        )

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
        assert (
            "AFFECTED_TEST_SELECTION_INSTALLED=tools/affected_test_selection.py"
            in upgraded.stdout
        )
        assert (
            "TERMINAL_COMPLETION_NOTIFY_INSTALLED=tools/terminal_completion_notify.py"
            in upgraded.stdout
        )
        assert helper.read_bytes() == (ROOT / "tools" / "context_epoch.py").read_bytes()
        assert affected_selector.read_bytes() == (
            ROOT / "tools" / "affected_test_selection.py"
        ).read_bytes()
        assert notifier.read_bytes() == (
            ROOT / "tools" / "terminal_completion_notify.py"
        ).read_bytes()
        checked = run(sys.executable, str(CHECK), "--root", str(target))
        assert "ENGINEERING_SYSTEM_ADOPTION=PASS" in checked.stdout

        packet_template = target / ".github/ISSUE_TEMPLATE/ai-work-packet.md"
        helper.unlink()
        packet_template.unlink()
        commit_all(target, "interrupt current-baseline managed surface writes")
        repaired = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "CONTEXT_EPOCH_REPAIR=REQUIRED" in repaired.stdout
        assert "WORK_PACKET_TEMPLATE_REPAIR=REQUIRED" in repaired.stdout
        assert "CONTEXT_EPOCH_INSTALLED=tools/context_epoch.py" in repaired.stdout
        assert (
            "WORK_PACKET_TEMPLATE_SYNCED=.github/ISSUE_TEMPLATE/ai-work-packet.md"
            in repaired.stdout
        )
        assert "ADOPTION_UPGRADE=PASS" in repaired.stdout
        assert "ADOPTION_UPGRADE=NO_CHANGE" not in repaired.stdout
        assert helper.read_bytes() == (ROOT / "tools" / "context_epoch.py").read_bytes()
        assert packet_template.read_bytes() == (
            ROOT / ".github/ISSUE_TEMPLATE/ai-work-packet.md"
        ).read_bytes()
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
        helper.unlink()
        compliance = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert compliance.returncode != 0
        assert "managed-profile adoption missing required helper tools/engineering-context.py" in compliance.stdout

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


def test_local_adoption_checker_profile_contract_is_not_target_version_gated() -> None:
    checker = CHECK.read_text(encoding="utf-8")
    required = (
        "canonical_checker_version",
        "checker_managed_profile = version_at_least(checker_version, (1, 6, 5))",
        "engineering_system.version does not match canonical checker version",
        "if checker_managed_profile:",
    )
    for token in required:
        assert token in checker, token
    assert "if version_at_least(version, (1, 6, 5)):" not in checker


def test_adoption_checker_rejects_stale_policy_and_profile() -> None:
    checker = CHECK.read_text(encoding="utf-8")
    required = (
        "stale adoption policy_epoch:",
        "stale adoption execution profile:",
        "canonical_policy_epoch",
        "canonical_profile",
        "--require-current-policy",
    )
    for token in required:
        assert token in checker, token


def test_adoption_checker_requires_explicit_governance_epoch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-missing-governance-epoch"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/missing-governance-epoch\n\ngo 1.23\n",
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
        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        del project["engineering_system"]["governance_epoch"]
        project_path.write_text(
            yaml.safe_dump(project, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        checked = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert checked.returncode != 0
        assert (
            "requires explicit engineering_system.governance_epoch"
            in checked.stdout
        )


def test_adoption_compliance_workflow_checks_engineering_context_helper() -> None:
    workflow = (ROOT / ".github" / "workflows" / "adoption-compliance.yml").read_text(
        encoding="utf-8"
    )
    required = (
        "tools/check-adoption.py",
        "tools/adopt.py",
        "tools/execution_profile.py",
        "tools/engineering-context.py",
        "tools/governance_floor.py",
        "tools/implementation_preflight.py",
        "tools/work_packet_authority.py",
        "tools/terminal_completion_notify.py",
        ".engineering/execution-profile.yaml",
        "schemas/execution-profile.schema.json",
        "CALLED_WORKFLOW_SHA: ${{ job.workflow_sha }}",
        '--expected-baseline "$CALLED_WORKFLOW_SHA"',
    )
    for token in required:
        assert token in workflow, token
    assert 'if "Cursor" in text' not in workflow
    assert "canonical_helper = Path" not in workflow


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




def test_managed_contract_dependency_failure_is_deterministic() -> None:
    requirements = ROOT / ".engineering" / "requirements-engineering-system.txt"
    text = requirements.read_text(encoding="utf-8")
    assert "PyYAML==6.0.2" in text
    assert "jsonschema==4.25.1" in text
    install = (
        "python3 -m pip install --disable-pip-version-check "
        "-r .engineering/requirements-engineering-system.txt"
    )
    for rel in (
        "tools/knowledge-contract.py",
        "tools/runtime-contract.py",
        "tools/skills-contract.py",
        "tools/verification-contract.py",
    ):
        completed = run(sys.executable, "-S", str(ROOT / rel), "--help", check=False)
        assert completed.returncode != 0, rel
        assert "ENGINEERING_SYSTEM_DEPENDENCY_MISSING=" in completed.stdout, (rel, completed.stdout)
        assert install in completed.stdout, (rel, completed.stdout)
        assert "Traceback" not in completed.stdout, (rel, completed.stdout)



def test_adoption_checker_expected_mode_is_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-expected-mode"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/expected-mode\n\ngo 1.23\n",
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
        project_path = target / ".engineering/project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["mode"] = "canonical"
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        blocked = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            "--expected-baseline",
            BASELINE,
            "--expected-mode",
            "adopted",
            check=False,
        )
        assert blocked.returncode != 0
        assert "engineering_system.mode does not match expected mode: adopted" in blocked.stdout



def test_managed_work_packet_template_requires_v3_profile_metadata() -> None:
    template = (ROOT / "templates/.github/ISSUE_TEMPLATE/ai-work-packet.md").read_text(
        encoding="utf-8"
    )
    for key in ("INTENT_REVISION", "CHANGE_RISK", "EXECUTION_PROFILE", "EXECUTION_PROFILE_REVISION"):
        assert re.search(rf"(?m)^{key}=", template), key
    checker = CHECK.read_text(encoding="utf-8")
    assert "managed Work Packet template missing required packet-v3 metadata" in checker


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


def test_user_facing_browser_release_requires_human_equivalent_contracts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-browser-product"
        target.mkdir()
        init_repo(target)
        (target / "tests").mkdir()
        (target / "tests/test_demo.py").write_text("def test_demo():\n    assert True\n", encoding="utf-8")
        (target / "docs").mkdir()
        (target / "docs/SURFACE_RECONCILIATION.md").write_text("# Surface Reconciliation\n", encoding="utf-8")
        (target / "docs/FULL_USER_E2E.md").write_text("# Full User E2E\n", encoding="utf-8")
        commit_all(target)

        audit_review = run(
            sys.executable, str(ADOPT),
            "--root", str(target),
            "--audit",
            "--baseline-sha", BASELINE,
            "--test-command", "python -m pytest -q",
            "--user-facing",
            "--primary-user-surface", "browser",
            "--surface-reconciliation-contract", "docs/SURFACE_RECONCILIATION.md",
            "--full-user-e2e-contract", "docs/FULL_USER_E2E.md",
        )
        assert audit_review.returncode == 0
        assert "ADOPTION_AUDIT=PASS" in audit_review.stdout
        assert not (target / ".engineering").exists()

        blocked_review = run(
            sys.executable, str(ADOPT),
            "--root", str(target),
            "--apply",
            "--baseline-sha", BASELINE,
            "--test-command", "python -m pytest -q",
            "--user-facing",
            "--primary-user-surface", "browser",
            "--surface-reconciliation-contract", "docs/SURFACE_RECONCILIATION.md",
            "--full-user-e2e-contract", "docs/FULL_USER_E2E.md",
            check=False,
        )
        assert blocked_review.returncode != 0
        assert "--user-gate-contracts-reviewed" in blocked_review.stdout
        assert not (target / ".engineering").exists()

        applied = run(
            sys.executable, str(ADOPT),
            "--root", str(target),
            "--apply",
            "--baseline-sha", BASELINE,
            "--test-command", "python -m pytest -q",
            "--user-facing",
            "--user-gate-contracts-reviewed",
            "--primary-user-surface", "browser",
            "--surface-reconciliation-contract", "docs/SURFACE_RECONCILIATION.md",
            "--full-user-e2e-contract", "docs/FULL_USER_E2E.md",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout
        project = load_yaml(target / ".engineering/project.yaml")
        assert project["project"]["user_facing"] is True
        assert project["project"]["primary_user_surface"] == "browser"
        release = load_yaml(target / ".engineering/release.yaml")
        assert release["human_equivalent_user_tests_required"] is True
        user_tests = release["human_equivalent_user_tests"]
        assert user_tests["contract_version"] == 2
        assert user_tests["executor"] == "EXECUTION_PROFILE"
        assert user_tests["direct_persona_execution_required"] is True
        assert user_tests["canonical_contract_read_before_execution_required"] is True
        assert user_tests["complete_rerun_after_remediation_required"] is True
        assert user_tests["wrapper_user_substitution_forbidden"] is True
        assert user_tests["contract_review_attestation_version"] == 1
        assert user_tests["actual_user_surface_required"] is True
        assert user_tests["actual_browser_process_required"] is True
        assert user_tests["same_candidate_required"] is True
        assert user_tests["finding_accumulation_before_remediation"] is True
        assert user_tests["same_head_quality_closure_required"] is True
        assert user_tests["candidate_freeze_after_quality_closure"] is True
        assert user_tests["ci_contract_validation_only"] is True
        assert user_tests["evidence_validator"] == "tools/user_acceptance_contract.py"
        assert user_tests["surface_reconciliation"]["contract"] == "docs/SURFACE_RECONCILIATION.md"
        assert user_tests["full_user_e2e"]["contract"] == "docs/FULL_USER_E2E.md"
        assert (target / "tools/user_acceptance_contract.py").is_file()
        assert (target / "schemas/user-acceptance-evidence.schema.json").is_file()
        assert run(sys.executable, str(CHECK), "--root", str(target)).returncode == 0

        for invalid_required in ("true", "false", 1):
            release["human_equivalent_user_tests_required"] = invalid_required
            (target / ".engineering/release.yaml").write_text(
                yaml.safe_dump(release, sort_keys=False),
                encoding="utf-8",
            )
            blocked = run(sys.executable, str(CHECK), "--root", str(target), check=False)
            assert blocked.returncode != 0
            assert "user-facing project requires human_equivalent_user_tests_required=true" in blocked.stdout
        release["human_equivalent_user_tests_required"] = True

        release["human_equivalent_user_tests"]["actual_browser_process_required"] = False
        (target / ".engineering/release.yaml").write_text(yaml.safe_dump(release, sort_keys=False), encoding="utf-8")
        blocked = run(sys.executable, str(CHECK), "--root", str(target), check=False)
        assert blocked.returncode != 0
        assert "browser user-facing project requires actual_browser_process_required=true" in blocked.stdout

        release["human_equivalent_user_tests"]["actual_browser_process_required"] = True
        release["human_equivalent_user_tests"]["direct_persona_execution_required"] = False
        (target / ".engineering/release.yaml").write_text(yaml.safe_dump(release, sort_keys=False), encoding="utf-8")
        blocked = run(sys.executable, str(CHECK), "--root", str(target), check=False)
        assert blocked.returncode != 0
        assert "human-equivalent user tests require direct_persona_execution_required=true" in blocked.stdout

        for field, message in (
            (
                "canonical_contract_read_before_execution_required",
                "human-equivalent user tests require canonical_contract_read_before_execution_required=true",
            ),
            (
                "complete_rerun_after_remediation_required",
                "human-equivalent user tests require complete_rerun_after_remediation_required=true",
            ),
            (
                "wrapper_user_substitution_forbidden",
                "human-equivalent user tests require wrapper_user_substitution_forbidden=true",
            ),
        ):
            release = load_yaml(target / ".engineering/release.yaml")
            release["human_equivalent_user_tests"]["direct_persona_execution_required"] = True
            release["human_equivalent_user_tests"][field] = False
            (target / ".engineering/release.yaml").write_text(
                yaml.safe_dump(release, sort_keys=False), encoding="utf-8"
            )
            blocked = run(sys.executable, str(CHECK), "--root", str(target), check=False)
            assert blocked.returncode != 0
            assert message in blocked.stdout


def test_managed_upgrade_migrates_legacy_release_executor_and_rejects_custom() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-release-executor-upgrade"
        target.mkdir()
        init_repo(target)
        (target / "tests").mkdir()
        (target / "tests/test_demo.py").write_text(
            "def test_demo():\n    assert True\n",
            encoding="utf-8",
        )
        (target / "docs").mkdir()
        (target / "docs/SURFACE_RECONCILIATION.md").write_text(
            "# Surface Reconciliation\n",
            encoding="utf-8",
        )
        (target / "docs/FULL_USER_E2E.md").write_text(
            "# Full User E2E\n",
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
            "python -m pytest -q",
            "--user-facing",
            "--user-gate-contracts-reviewed",
            "--primary-user-surface",
            "browser",
            "--surface-reconciliation-contract",
            "docs/SURFACE_RECONCILIATION.md",
            "--full-user-e2e-contract",
            "docs/FULL_USER_E2E.md",
        )
        commit_all(target, "adopt user-facing target")

        release_path = target / ".engineering/release.yaml"
        release = load_yaml(release_path)
        user_tests = release["human_equivalent_user_tests"]
        user_tests["executor"] = "CHATGPT_CHAT"
        for key in (
            "contract_version",
            "direct_persona_execution_required",
            "canonical_contract_read_before_execution_required",
            "complete_rerun_after_remediation_required",
            "wrapper_user_substitution_forbidden",
            "contract_review_attestation_version",
            "finding_accumulation_before_remediation",
            "same_head_quality_closure_required",
            "candidate_freeze_after_quality_closure",
            "evidence_validator",
        ):
            user_tests.pop(key, None)
        release_path.write_text(
            yaml.safe_dump(release, sort_keys=False),
            encoding="utf-8",
        )
        commit_all(target, "legacy managed release user-test contract")

        checker = run(
            sys.executable,
            str(CHECK),
            "--root",
            str(target),
            check=False,
        )
        assert checker.returncode != 0
        assert (
            "human-equivalent user tests executor must be EXECUTION_PROFILE"
            in checker.stdout
        )

        before_review = release_path.read_bytes()
        audit_review = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--audit",
            "--baseline-sha",
            BASELINE,
        )
        assert "USER_GATE_CONTRACT_REVIEW=REQUIRED" in audit_review.stdout
        assert "RELEASE_EXECUTOR_MIGRATION=REQUIRED" in audit_review.stdout
        assert "ADOPTION_UPGRADE_AUDIT=PASS" in audit_review.stdout
        assert release_path.read_bytes() == before_review

        blocked_review = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            check=False,
        )
        assert blocked_review.returncode != 0
        assert "--user-gate-contracts-reviewed" in blocked_review.stdout
        assert release_path.read_bytes() == before_review

        blocked_audit_apply = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--audit",
            "--apply",
            "--baseline-sha",
            BASELINE,
            check=False,
        )
        assert blocked_audit_apply.returncode != 0
        assert "--user-gate-contracts-reviewed" in blocked_audit_apply.stdout
        assert release_path.read_bytes() == before_review

        repaired = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--user-gate-contracts-reviewed",
        )
        assert "RELEASE_EXECUTOR_MIGRATION=REQUIRED" in repaired.stdout
        assert "RELEASE_EXECUTOR_SYNCED=YES" in repaired.stdout
        repaired_release = load_yaml(release_path)
        repaired_user_tests = repaired_release["human_equivalent_user_tests"]
        assert repaired_user_tests["executor"] == "EXECUTION_PROFILE"
        assert repaired_user_tests["contract_version"] == 2
        assert repaired_user_tests["direct_persona_execution_required"] is True
        assert repaired_user_tests["canonical_contract_read_before_execution_required"] is True
        assert repaired_user_tests["complete_rerun_after_remediation_required"] is True
        assert repaired_user_tests["wrapper_user_substitution_forbidden"] is True
        assert repaired_user_tests["contract_review_attestation_version"] == 1
        assert repaired_user_tests["finding_accumulation_before_remediation"] is True
        assert repaired_user_tests["same_head_quality_closure_required"] is True
        assert repaired_user_tests["candidate_freeze_after_quality_closure"] is True
        assert repaired_user_tests["evidence_validator"] == "tools/user_acceptance_contract.py"
        assert (target / "tools/user_acceptance_contract.py").is_file()
        assert (target / "schemas/user-acceptance-evidence.schema.json").is_file()
        assert run(
            sys.executable, str(CHECK), "--root", str(target)
        ).returncode == 0
        commit_all(target, "migrate release executor")

        # Simulate the brief pre-corrective baseline that wrote the semantic
        # booleans true without reviewed provenance. Booleans alone must not
        # allow a later upgrade to self-certify the local contracts.
        pre_fix_release = load_yaml(release_path)
        pre_fix_release["human_equivalent_user_tests"].pop(
            "contract_review_attestation_version", None
        )
        release_path.write_text(
            yaml.safe_dump(pre_fix_release, sort_keys=False), encoding="utf-8"
        )
        commit_all(target, "simulate pre-fix unreviewed declarations")
        before_attestation = release_path.read_bytes()
        blocked_attestation = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            check=False,
        )
        assert blocked_attestation.returncode != 0
        assert "no reviewed provenance" in blocked_attestation.stdout
        assert release_path.read_bytes() == before_attestation

        attested = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--user-gate-contracts-reviewed",
        )
        assert "RELEASE_EXECUTOR_MIGRATION=REQUIRED" in attested.stdout
        attested_release = load_yaml(release_path)
        assert (
            attested_release["human_equivalent_user_tests"][
                "contract_review_attestation_version"
            ]
            == 1
        )
        commit_all(target, "attest reviewed user gate contracts")

        attested_bytes = release_path.read_bytes()
        for invalid_attestation in (True, 1.0):
            invalid_release = load_yaml(release_path)
            invalid_release["human_equivalent_user_tests"][
                "contract_review_attestation_version"
            ] = invalid_attestation
            release_path.write_text(
                yaml.safe_dump(invalid_release, sort_keys=False), encoding="utf-8"
            )
            commit_all(target, f"invalid attestation {invalid_attestation!r}")

            checker_invalid = run(
                sys.executable, str(CHECK), "--root", str(target), check=False
            )
            assert checker_invalid.returncode != 0
            assert "invalid contract_review_attestation_version; manual review required" in checker_invalid.stdout

            upgrade_invalid = run(
                sys.executable,
                str(UPGRADE),
                "--root",
                str(target),
                "--apply",
                "--baseline-sha",
                BASELINE,
                "--user-gate-contracts-reviewed",
                check=False,
            )
            assert upgrade_invalid.returncode != 0
            assert "contract_review_attestation_version contains local/custom changes" in (
                upgrade_invalid.stdout
            )

            release_path.write_bytes(attested_bytes)
            commit_all(target, "restore valid review attestation")

        custom_release = load_yaml(release_path)
        custom_release["human_equivalent_user_tests"]["executor"] = "CUSTOM_RUNNER"
        release_path.write_text(
            yaml.safe_dump(custom_release, sort_keys=False),
            encoding="utf-8",
        )
        commit_all(target, "custom release executor")
        before = release_path.read_bytes()
        blocked = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            check=False,
        )
        assert blocked.returncode != 0
        assert "release human-equivalent executor contains local/custom changes" in (
            blocked.stdout
        )
        assert release_path.read_bytes() == before


def test_user_facing_adoption_fails_without_contracts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-missing-user-contracts"
        target.mkdir()
        init_repo(target)
        (target / "README.md").write_text("# Demo\n", encoding="utf-8")
        commit_all(target)
        blocked = run(
            sys.executable, str(ADOPT),
            "--root", str(target),
            "--apply",
            "--baseline-sha", BASELINE,
            "--allow-no-tests",
            "--user-facing",
            "--user-gate-contracts-reviewed",
            "--primary-user-surface", "browser",
            check=False,
        )
        assert blocked.returncode != 0
        assert "requires both --surface-reconciliation-contract and --full-user-e2e-contract" in blocked.stdout


def test_user_facing_contract_paths_are_repository_bounded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        target = tmp_root / "demo-bounded-contracts"
        target.mkdir()
        init_repo(target)
        (target / "tests").mkdir()
        (target / "tests/test_demo.py").write_text("def test_demo():\n    assert True\n", encoding="utf-8")
        (target / "docs").mkdir()
        surface = target / "docs/SURFACE_RECONCILIATION.md"
        full_e2e = target / "docs/FULL_USER_E2E.md"
        surface.write_text("# Surface Reconciliation\n", encoding="utf-8")
        full_e2e.write_text("# Full User E2E\n", encoding="utf-8")
        outside = tmp_root / "OUTSIDE.md"
        outside.write_text("# Outside\n", encoding="utf-8")
        (target / "docs/ESCAPE.md").symlink_to(outside)
        commit_all(target)

        invalid_paths = (
            str(surface.resolve()),
            "docs/../docs/SURFACE_RECONCILIATION.md",
            "docs/ESCAPE.md",
        )
        for invalid_surface in invalid_paths:
            blocked = run(
                sys.executable, str(ADOPT),
                "--root", str(target),
                "--apply",
                "--baseline-sha", BASELINE,
                "--test-command", "python -m pytest -q",
                "--user-facing",
                "--user-gate-contracts-reviewed",
                "--primary-user-surface", "browser",
                "--surface-reconciliation-contract", invalid_surface,
                "--full-user-e2e-contract", "docs/FULL_USER_E2E.md",
                check=False,
            )
            assert blocked.returncode != 0
            assert "contract path must be repository-relative and stay inside repository" in blocked.stdout

        applied = run(
            sys.executable, str(ADOPT),
            "--root", str(target),
            "--apply",
            "--baseline-sha", BASELINE,
            "--test-command", "python -m pytest -q",
            "--user-facing",
            "--user-gate-contracts-reviewed",
            "--primary-user-surface", "browser",
            "--surface-reconciliation-contract", "docs/SURFACE_RECONCILIATION.md",
            "--full-user-e2e-contract", "docs/FULL_USER_E2E.md",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout

        release_path = target / ".engineering/release.yaml"
        for invalid_surface in invalid_paths:
            release = load_yaml(release_path)
            release["human_equivalent_user_tests"]["surface_reconciliation"]["contract"] = invalid_surface
            release_path.write_text(yaml.safe_dump(release, sort_keys=False), encoding="utf-8")
            blocked = run(sys.executable, str(CHECK), "--root", str(target), check=False)
            assert blocked.returncode != 0
            assert "contract must be repository-relative and stay inside repository" in blocked.stdout


def test_adoption_compliance_workflow_enforces_user_facing_release_gates() -> None:
    workflow = (ROOT / ".github/workflows/adoption-compliance.yml").read_text(encoding="utf-8")
    checker = CHECK.read_text(encoding="utf-8")
    assert "tools/check-adoption.py" in workflow
    assert '--expected-baseline "$CALLED_WORKFLOW_SHA"' in workflow
    assert "--expected-mode adopted" in workflow
    for needle in (
        "user-facing project requires human_equivalent_user_tests_required=true",
        "human-equivalent user tests require contract_version=2",
        "human-equivalent user tests require direct_persona_execution_required=true",
        "human-equivalent user tests require canonical_contract_read_before_execution_required=true",
        "human-equivalent user tests require complete_rerun_after_remediation_required=true",
        "human-equivalent user tests require wrapper_user_substitution_forbidden=true",
        "human-equivalent user tests missing contract_review_attestation_version=1 reviewed provenance",
        "human-equivalent user tests have invalid contract_review_attestation_version; manual review required",
        "human-equivalent user tests require actual_user_surface_required=true",
        "human-equivalent user tests require finding_accumulation_before_remediation=true",
        "human-equivalent user tests require same_head_quality_closure_required=true",
        "human-equivalent user tests require candidate_freeze_after_quality_closure=true",
        "human-equivalent user tests require managed user acceptance evidence validator",
        "browser user-facing project requires actual_browser_process_required=true",
        'for gate_name in ("surface_reconciliation", "full_user_e2e")',
        'failures.append(f"human-equivalent {gate_name} gate must be mandatory")',
        'release.get("human_equivalent_user_tests_required") is not True',
        "relative.is_absolute()",
        '".." in relative.parts',
        "candidate.relative_to(root)",
    ):
        assert needle in checker, needle


def test_same_baseline_execution_profile_repair_advances_epoch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-profile-repair"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/profile-repair\n\ngo 1.23\n",
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
        commit_all(target, "adopt current baseline")

        project_path = target / ".engineering/project.yaml"
        before_engineering = load_yaml(project_path)["engineering_system"]
        before_epoch = before_engineering["policy_epoch"]
        before_governance_epoch = before_engineering["governance_epoch"]
        for rel in (
            ".engineering/execution-profile.yaml",
            "tools/execution_profile.py",
            "schemas/execution-profile.schema.json",
        ):
            (target / rel).unlink()
        commit_all(target, "remove managed execution profile bundle")
        damaged_base = run("git", "rev-parse", "HEAD", cwd=target).stdout.strip()

        repaired = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
        )
        assert "EXECUTION_PROFILE_REPAIR=REQUIRED" in repaired.stdout
        assert f"GOVERNANCE_EPOCH_REPAIR={before_governance_epoch + 1}" in repaired.stdout
        assert "ADOPTION_UPGRADE=PASS" in repaired.stdout
        after_engineering = load_yaml(project_path)["engineering_system"]
        assert after_engineering["policy_epoch"] == before_epoch
        assert after_engineering["governance_epoch"] == before_governance_epoch + 1
        expected_profile_surfaces = {
            ".engineering/execution-profile.yaml",
            "tools/execution_profile.py",
            "schemas/execution-profile.schema.json",
        }
        for rel in expected_profile_surfaces:
            assert (target / rel).is_file(), rel
        migration = load_yaml(target / ".engineering/governance-migration.yaml")
        assert migration["contract_version"] == 2
        assert migration["from_governance_epoch"] == before_governance_epoch
        assert migration["to_governance_epoch"] == before_governance_epoch + 1
        assert expected_profile_surfaces.issubset(
            {entry["path"] for entry in migration["changed_surfaces"]}
        )
        commit_all(target, "repair managed execution profile bundle")
        repaired_head = run("git", "rev-parse", "HEAD", cwd=target).stdout.strip()
        floor_check = run(
            sys.executable,
            str(target / "tools/governance_floor.py"),
            "check",
            "--root",
            str(target),
            "--base-ref",
            damaged_base,
            "--head-ref",
            repaired_head,
            check=False,
        )
        assert floor_check.returncode == 0, floor_check.stdout
        assert "GOVERNANCE_FLOOR=PASS" in floor_check.stdout


def test_same_baseline_partial_execution_profile_repair_records_manifest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-profile-partial-repair"
        target.mkdir()
        init_repo(target)
        (target / "go.mod").write_text(
            "module example.invalid/profile-partial-repair\n\ngo 1.23\n",
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
        commit_all(target, "adopt current baseline")

        project_path = target / ".engineering/project.yaml"
        before_engineering = load_yaml(project_path)["engineering_system"]
        before_epoch = before_engineering["policy_epoch"]
        before_governance_epoch = before_engineering["governance_epoch"]
        (target / "tools/execution_profile.py").unlink()
        commit_all(target, "remove managed execution profile helper only")
        base_head = run("git", "rev-parse", "HEAD", cwd=target).stdout.strip()

        repaired = run(
            sys.executable,
            str(UPGRADE),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
        )
        assert "EXECUTION_PROFILE_REPAIR=REQUIRED" in repaired.stdout
        assert "GOVERNANCE_ROOT_MIGRATION=REQUIRED" in repaired.stdout
        assert f"GOVERNANCE_EPOCH_REPAIR={before_governance_epoch + 1}" in repaired.stdout
        migration = load_yaml(target / ".engineering/governance-migration.yaml")
        assert migration["contract_version"] == 2
        assert migration["from_governance_epoch"] == before_governance_epoch
        assert migration["to_governance_epoch"] == before_governance_epoch + 1
        surfaces = {entry["path"] for entry in migration["changed_surfaces"]}
        assert "tools/execution_profile.py" in surfaces
        assert migration["base_sha"] == base_head
        helper_blob = run(
            "git", "hash-object", "tools/execution_profile.py", cwd=target
        ).stdout.strip()
        entry = next(
            item for item in migration["changed_surfaces"]
            if item["path"] == "tools/execution_profile.py"
        )
        assert entry["head_blob_sha"] == helper_blob
        repaired_engineering = load_yaml(project_path)["engineering_system"]
        assert repaired_engineering["policy_epoch"] == before_epoch
        assert repaired_engineering["governance_epoch"] == before_governance_epoch + 1


def test_obsolete_coordinator_gate_removed_without_weakening_product_approval() -> None:
    legacy = (
        "Before mutation, the external authenticated GitHub coordinator must verify the current Work Packet, "
        "author permission, repository, worktree, branch, exact HEAD, intent revision, change risk, and "
        "`IMPLEMENTER=CHATGPT_CHAT`. The worker-writable repository copy of "
        "`python3 tools/implementation_preflight.py check` is never mutation authority. Use the helper source "
        "from the immutable pinned Engineering System baseline through the isolated trusted launcher, "
        "capture the no-follow worktree identity, and require `IMPLEMENTATION_LOCAL_BINDING=PASS` with "
        "`MUTATION_AUTHORITY=NO`."
    )
    product = "Production writes require explicit owner approval; preserve user data and release gates."
    cleaned = rewrite_legacy_coordination_rules(legacy + "\n\n" + product + "\n")
    assert legacy not in cleaned
    assert product in cleaned
    assert rewrite_legacy_coordination_rules(cleaned) == cleaned


def test_legacy_rule_suffix_preserves_project_policy() -> None:
    product = "Production writes require explicit owner approval; preserve user data."
    lines = ["- Reconcile one Work Packet's next action with `python3 tools/coordinator.py plan --facts <facts.json>`. The planner is pure: one bounded decision, no worker launch, GitHub mutation, merge, notification send, or session stop.", '- Evaluate one bounded coordinator watch with `python3 tools/coordinator_watch.py evaluate --facts <facts.json> --watch-state <state.json>`. The evaluator is pure: one re-entry result, no subprocess, network, GitHub mutation, merge, or Telegram send.', '- Run one coordinator watch host pass with `python3 tools/coordinator_watch_host.py run-once --request <request.json>`. The host acquires one lock, calls the watch evaluator, and delivers at most one already-authorized typed action after a fresh reconciliation read. It does not accept caller commands or URLs, mint authority, merge, stop sessions, or busy-loop.', '- Do not keep a coding-agent session alive polling CI/review/external waits; persist concise state and yield to coordinator/automation.']
    for legacy_line in lines:
        cleaned = rewrite_legacy_coordination_rules(legacy_line + " " + product + chr(10))
        assert product in cleaned
        assert legacy_line not in cleaned
        assert rewrite_legacy_coordination_rules(cleaned) == cleaned
    for legacy_line in ['- Do not spend coding-agent model time polling CI, review, or another machine-observable external wait. Persist concise waiting state and yield to coordinator/automation for re-entry.']:
        cleaned = rewrite_legacy_coordination_rules(legacy_line + " " + product + chr(10))
        assert legacy_line not in cleaned
        assert product in cleaned and "continue independent authorized work" in cleaned
        assert rewrite_legacy_coordination_rules(cleaned) == cleaned
    unknown = "- Reconcile one Work Packet's next action with custom product coordination; " + product
    assert unknown in rewrite_legacy_coordination_rules(unknown + chr(10))


def main() -> int:
    run(sys.executable, "-m", "py_compile", str(ADOPT), str(CHECK), str(UPGRADE))
    test_obsolete_coordinator_gate_removed_without_weakening_product_approval()
    test_legacy_rule_suffix_preserves_project_policy()
    test_clean_python_bootstrap()
    test_rule_review_is_fail_closed()
    test_existing_ci_requires_mapping_when_ambiguous()
    test_operations_signals_fail_closed_then_production_profile()
    test_quality_and_domain_discovery()
    test_managed_upgrade_to_1_6()
    test_generated_agents_references_only_managed_tools()
    test_general_upgrade_requires_stage_a_bridge_before_profile_v3()
    test_same_baseline_governance_floor_repair_emits_root_migration()
    test_legacy_root_repair_cannot_exceed_canonical_policy()
    test_v2_same_baseline_rejects_newer_policy()
    test_same_baseline_execution_profile_repair_advances_epoch()
    test_same_baseline_partial_execution_profile_repair_records_manifest()
    test_same_baseline_repairs_managed_execution_policy()
    test_managed_upgrade_rejects_unclassified_next_chat_policy_suffixes()
    test_managed_upgrade_rejects_ambiguous_next_chat_policy_edit()
    test_unknown_cursor_agent_rule_fails_closed_before_upgrade()
    test_managed_file_hash_manifests_match_immutable_revisions()
    test_same_baseline_repairs_prior_1_7_managed_bytes_after_metadata_stamp()
    test_same_baseline_repairs_stage_a_managed_bytes_after_metadata_stamp()
    test_managed_file_hash_history_upgrades_known_bytes_and_rejects_custom()
    test_managed_file_old_baseline_is_trusted_without_manifest()
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
    test_local_adoption_checker_profile_contract_is_not_target_version_gated()
    test_adoption_checker_rejects_stale_policy_and_profile()
    test_adoption_checker_requires_explicit_governance_epoch()
    test_adoption_compliance_workflow_checks_engineering_context_helper()
    test_release_execution_context_is_bounded_and_upgradeable()
    test_managed_contract_dependency_failure_is_deterministic()
    test_adoption_checker_expected_mode_is_fail_closed()
    test_managed_work_packet_template_requires_v3_profile_metadata()
    test_user_facing_browser_release_requires_human_equivalent_contracts()
    test_managed_upgrade_migrates_legacy_release_executor_and_rejects_custom()
    test_user_facing_adoption_fails_without_contracts()
    test_user_facing_contract_paths_are_repository_bounded()
    test_adoption_compliance_workflow_enforces_user_facing_release_gates()
    test_bun_native_discovery()
    print("ADOPTION_TOOL_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
