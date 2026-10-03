#!/usr/bin/env python3
"""Regression tests for organization-wide Engineering System rollout."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ADOPT = ROOT / "tools" / "adopt.py"
UPGRADE = ROOT / "tools" / "upgrade-adoption.py"
ROLLOUT = ROOT / "tools" / "org-rollout.py"
BASELINE = "a" * 40
NEW_BASELINE = "b" * 40

_SPEC = importlib.util.spec_from_file_location("org_rollout", ROLLOUT)
assert _SPEC and _SPEC.loader
org_rollout = importlib.util.module_from_spec(_SPEC)
sys.modules["org_rollout"] = org_rollout
_SPEC.loader.exec_module(org_rollout)


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
    run("git", "config", "user.name", "Rollout Test", cwd=root)
    run("git", "branch", "-M", "main", cwd=root)


def commit_all(root: Path, message: str = "fixture") -> None:
    run("git", "add", ".", cwd=root)
    run("git", "commit", "-qm", message, cwd=root)


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def adopt_python(target: Path, baseline: str = BASELINE) -> None:
    (target / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (target / "tests").mkdir(exist_ok=True)
    (target / "tests" / "test_demo.py").write_text("def test_demo():\n    assert True\n", encoding="utf-8")
    commit_all(target, "product fixture")
    run(
        sys.executable,
        str(ADOPT),
        "--root",
        str(target),
        "--apply",
        "--baseline-sha",
        baseline,
        "--test-command",
        "python -m pytest -q",
    )
    commit_all(target, "adopt engineering system")


def adopt_user_facing(target: Path, baseline: str = BASELINE) -> None:
    (target / "pyproject.toml").write_text("[project]\nname='demo-user'\n", encoding="utf-8")
    (target / "tests").mkdir(exist_ok=True)
    (target / "tests" / "test_demo.py").write_text("def test_demo():\n    assert True\n", encoding="utf-8")
    (target / "docs").mkdir(exist_ok=True)
    (target / "docs/SURFACE_RECONCILIATION.md").write_text(
        "# Surface Reconciliation\n", encoding="utf-8"
    )
    (target / "docs/FULL_USER_E2E.md").write_text(
        "# Full User E2E\n", encoding="utf-8"
    )
    commit_all(target, "user-facing product fixture")
    run(
        sys.executable,
        str(ADOPT),
        "--root",
        str(target),
        "--apply",
        "--baseline-sha",
        baseline,
        "--test-command",
        "python -m pytest -q",
        "--user-facing",
        "--user-gate-contracts-reviewed",
        "--primary-user-surface",
        "cli",
        "--surface-reconciliation-contract",
        "docs/SURFACE_RECONCILIATION.md",
        "--full-user-e2e-contract",
        "docs/FULL_USER_E2E.md",
    )
    commit_all(target, "adopt user-facing engineering system")


def write_inventory(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def test_flatten_paginated_inventory() -> None:
    page_one = [{"full_name": f"org/repo-{i}", "archived": False, "default_branch": "main"} for i in range(100)]
    page_two = [{"full_name": f"org/repo-{i}", "archived": False, "default_branch": "main"} for i in range(100, 105)]
    flattened = org_rollout.flatten_paginated_payload([page_one, page_two])
    assert len(flattened) == 105
    assert flattened[0]["full_name"] == "org/repo-0"
    assert flattened[-1]["full_name"] == "org/repo-104"
    # Single-page responses remain a flat list.
    assert len(org_rollout.flatten_paginated_payload(page_one)) == 100


def test_org_rollout_matrix() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        current = base / "current"
        outdated = base / "outdated"
        unadopted = base / "unadopted"
        archived = base / "archived"
        incomplete = base / "incomplete"

        for path in (current, outdated, unadopted, archived, incomplete):
            path.mkdir()
            init_repo(path)

        adopt_python(current, BASELINE)
        adopt_python(outdated, BASELINE)
        project_path = outdated / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.0"
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        commit_all(outdated, "mark adoption outdated")

        (unadopted / "README.md").write_text("unadopted\n", encoding="utf-8")
        commit_all(unadopted, "unadopted fixture")

        adopt_python(archived, BASELINE)
        (incomplete / ".engineering").mkdir()
        (incomplete / ".engineering" / "incomplete-marker").write_text("incomplete\n", encoding="utf-8")
        commit_all(incomplete, "incomplete adoption markers")

        inventory = base / "inventory.json"
        write_inventory(
            inventory,
            [
                {
                    "full_name": "demo/current",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(current),
                },
                {
                    "full_name": "demo/outdated",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(outdated),
                },
                {
                    "full_name": "demo/unadopted",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(unadopted),
                },
                {
                    "full_name": "demo/archived",
                    "archived": True,
                    "default_branch": "main",
                    "local_path": str(archived),
                },
                {
                    "full_name": "demo/incomplete",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(incomplete),
                },
            ],
        )

        audit = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(inventory),
            "--audit",
            "--baseline-sha",
            BASELINE,
        )
        assert "ORG_ROLLOUT_MODE=AUDIT" in audit.stdout
        assert "TARGET_BASELINE=" + BASELINE in audit.stdout
        assert "REPO=demo/current" in audit.stdout and "STATE=CURRENT" in audit.stdout
        assert "REPO=demo/outdated" in audit.stdout and "STATE=OUTDATED" in audit.stdout
        assert "ACTION=UPGRADE" in audit.stdout
        assert "REPO=demo/unadopted" in audit.stdout and "STATE=UNADOPTED" in audit.stdout
        assert "REPO=demo/archived" in audit.stdout and "ACTION=SKIP_ARCHIVED" in audit.stdout
        assert "REPO=demo/incomplete" in audit.stdout and "STATE=INCOMPLETE" in audit.stdout
        assert "ORG_ROLLOUT=PARTIAL" in audit.stdout or "ORG_ROLLOUT=PASS" in audit.stdout

        # Failure path: custom workflow blocks managed upgrade.
        broken = base / "broken"
        broken.mkdir()
        init_repo(broken)
        adopt_python(broken, BASELINE)
        project = load_yaml(broken / ".engineering" / "project.yaml")
        project["engineering_system"]["version"] = "1.5.0"
        (broken / ".engineering" / "project.yaml").write_text(
            yaml.safe_dump(project, sort_keys=False),
            encoding="utf-8",
        )
        workflow = broken / ".github" / "workflows" / "engineering-system.yml"
        workflow.write_text(workflow.read_text(encoding="utf-8") + "\n# local customization\n", encoding="utf-8")
        commit_all(broken, "break managed workflow")

        fail_inventory = base / "fail-inventory.json"
        write_inventory(
            fail_inventory,
            [
                {
                    "full_name": "demo/broken",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(broken),
                }
            ],
        )
        failed = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(fail_inventory),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert failed.returncode != 0
        assert "OUTCOME=FAIL" in failed.stdout
        assert "ORG_ROLLOUT=FAIL" in failed.stdout

        applied = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(inventory),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert "REPO=demo/outdated" in applied.stdout
        assert "OUTCOME=APPLIED" in applied.stdout
        assert "REPO=demo/unadopted" in applied.stdout
        assert "OUTCOME=NEEDS_INPUT" in applied.stdout
        assert "ORG_ROLLOUT=PARTIAL" in applied.stdout
        upgraded_project = load_yaml(outdated / ".engineering" / "project.yaml")
        assert upgraded_project["engineering_system"]["version"] == "1.7.0"
        assert upgraded_project["engineering_system"]["baseline"] == NEW_BASELINE
        branch = run("git", "branch", "--show-current", cwd=outdated)
        assert branch.stdout.strip().startswith("chore/engineering-system-rollout-")

        # Idempotent rerun against the upgraded checkout reports CURRENT/NO_CHANGE.
        rerun_inventory = base / "rerun-inventory.json"
        write_inventory(
            rerun_inventory,
            [
                {
                    "full_name": "demo/outdated",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(outdated),
                }
            ],
        )
        rerun = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(rerun_inventory),
            "--audit",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "STATE=CURRENT" in rerun.stdout
        assert "OUTCOME=NO_CHANGE" in rerun.stdout
        assert "ORG_ROLLOUT=PASS" in rerun.stdout


def test_same_version_different_baseline_is_outdated() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "same-version"
        repo.mkdir()
        init_repo(repo)
        adopt_python(repo, BASELINE)
        inventory = base / "inventory.json"
        write_inventory(
            inventory,
            [
                {
                    "full_name": "demo/same-version",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(repo),
                }
            ],
        )
        audit = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(inventory),
            "--audit",
            "--baseline-sha",
            NEW_BASELINE,
        )
        assert "STATE=OUTDATED" in audit.stdout
        assert "ACTION=UPGRADE" in audit.stdout
        assert "different baseline" in audit.stdout


def test_incomplete_surfaces_not_reported_current() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "partial"
        repo.mkdir()
        init_repo(repo)
        adopt_python(repo, BASELINE)
        (repo / ".engineering" / "tests.yaml").unlink()
        commit_all(repo, "remove tests.yaml")
        inventory = base / "inventory.json"
        write_inventory(
            inventory,
            [
                {
                    "full_name": "demo/partial",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(repo),
                }
            ],
        )
        audit = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(inventory),
            "--audit",
            "--baseline-sha",
            BASELINE,
        )
        assert "STATE=INCOMPLETE" in audit.stdout
        assert "OUTCOME=NEEDS_INPUT" in audit.stdout


def test_current_baseline_with_stale_execution_policy_is_repairable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "stale-policy"
        repo.mkdir()
        init_repo(repo)
        adopt_python(repo, BASELINE)
        agents_path = repo / "AGENTS.md"
        agents = agents_path.read_text(encoding="utf-8")
        policy_line = next(
            line for line in agents.splitlines()
            if line.startswith("- **Execute useful work continuously.**")
        )
        agents_path.write_text(
            agents.replace(policy_line + "\n", "", 1),
            encoding="utf-8",
        )
        commit_all(repo, "remove managed execution policy")

        inventory = base / "inventory.json"
        write_inventory(
            inventory,
            [{
                "full_name": "demo/stale-policy",
                "archived": False,
                "default_branch": "main",
                "local_path": str(repo),
            }],
        )
        audit = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(inventory),
            "--audit",
            "--baseline-sha",
            BASELINE,
        )
        assert "STATE=OUTDATED" in audit.stdout
        assert "ACTION=UPGRADE" in audit.stdout
        assert "AGENTS.md missing managed continuous-execution policy" in audit.stdout
        assert "OUTCOME=PLANNED" in audit.stdout



def test_current_baseline_with_managed_byte_drift_is_repairable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        packet_repo = base / "packet-drift"
        packet_repo.mkdir()
        init_repo(packet_repo)
        adopt_python(packet_repo, BASELINE)
        packet_path = packet_repo / ".github/ISSUE_TEMPLATE/ai-work-packet.md"
        packet = packet_path.read_text(encoding="utf-8")
        packet_path.write_text(
            "\n".join(
                line
                for line in packet.splitlines()
                if not line.startswith("EXECUTION_PROFILE_REVISION=")
            )
            + "\n",
            encoding="utf-8",
        )
        packet_result = org_rollout.classify_checkout(
            packet_repo, "1.7.0", BASELINE
        )
        assert packet_result.state == "OUTDATED", packet_result
        assert packet_result.action == "UPGRADE", packet_result
        assert "managed repair required" in packet_result.detail

        helper_repo = base / "helper-drift"
        helper_repo.mkdir()
        init_repo(helper_repo)
        adopt_python(helper_repo, BASELINE)
        helper_path = helper_repo / "tools/context_epoch.py"
        helper_path.write_text(
            helper_path.read_text(encoding="utf-8") + "\n# stale managed byte\n",
            encoding="utf-8",
        )
        helper_result = org_rollout.classify_checkout(
            helper_repo, "1.7.0", BASELINE
        )
        assert helper_result.state == "OUTDATED", helper_result
        assert helper_result.action == "UPGRADE", helper_result
        assert "managed repair required" in helper_result.detail

        governance_repo = base / "governance-floor-drift"
        governance_repo.mkdir()
        init_repo(governance_repo)
        adopt_python(governance_repo, BASELINE)
        governance_path = governance_repo / "tools/governance_floor.py"
        governance_path.write_text(
            governance_path.read_text(encoding="utf-8") + "\n# stale managed byte\n",
            encoding="utf-8",
        )
        governance_result = org_rollout.classify_checkout(
            governance_repo, "1.7.0", BASELINE
        )
        assert governance_result.state == "OUTDATED", governance_result
        assert governance_result.action == "UPGRADE", governance_result
        assert "tools/governance_floor.py differs from canonical managed helper" in (
            governance_result.detail
        )


def test_repair_markers_match_checker_diagnostics() -> None:
    assert "retired runtime artifact must be removed:" in (
        org_rollout.REPAIRABLE_STRUCTURAL_FAILURE_MARKERS
    )
    assert "managed-profile adoption missing required helper" in (
        org_rollout.REPAIRABLE_STRUCTURAL_FAILURE_MARKERS
    )
    assert "managed Work Packet template missing required packet-v3 metadata" in (
        org_rollout.REPAIRABLE_STRUCTURAL_FAILURE_MARKERS
    )
    assert "differs from canonical managed helper" in (
        org_rollout.REPAIRABLE_STRUCTURAL_FAILURE_MARKERS
    )
    assert "human-equivalent user tests executor must be EXECUTION_PROFILE" in (
        org_rollout.REPAIRABLE_STRUCTURAL_FAILURE_MARKERS
    )
    assert (
        "human-equivalent user tests missing contract_review_attestation_version=1 reviewed provenance"
        in org_rollout.REPAIRABLE_STRUCTURAL_FAILURE_MARKERS
    )
    assert org_rollout.repairable_structural_failure(
        "FAIL retired runtime artifact must be removed: .cursor"
    )
    assert org_rollout.repairable_structural_failure(
        "FAIL managed-profile adoption missing required helper tools/context_epoch.py"
    )
    assert org_rollout.repairable_structural_failure(
        "FAIL managed Work Packet template missing required packet-v3 metadata EXECUTION_PROFILE_REVISION"
    )
    assert org_rollout.repairable_structural_failure(
        "FAIL tools/context_epoch.py differs from canonical managed helper"
    )
    assert org_rollout.repairable_structural_failure(
        "FAIL human-equivalent user tests executor must be EXECUTION_PROFILE"
    )
    assert org_rollout.repairable_structural_failure(
        "FAIL human-equivalent user tests missing contract_review_attestation_version=1 reviewed provenance"
    )
    assert not org_rollout.repairable_structural_failure(
        "FAIL human-equivalent user tests have invalid contract_review_attestation_version; manual review required"
    )
    assert not org_rollout.repairable_structural_failure(
        "FAIL project-specific custom rule requires owner input"
    )
    assert not org_rollout.repairable_structural_failure(
        "FAIL human-equivalent user tests missing contract_review_attestation_version=1 reviewed provenance; "
        "FAIL human-equivalent surface_reconciliation contract missing: docs/SURFACE_RECONCILIATION.md"
    )
    assert org_rollout.repairable_structural_failure(
        "FAIL human-equivalent user tests missing contract_review_attestation_version=1 reviewed provenance; "
        "FAIL human-equivalent user tests executor must be EXECUTION_PROFILE"
    )
    assert not org_rollout.repairable_structural_failure(
        "FAIL human-equivalent user tests missing contract_review_attestation_version=1 reviewed provenance\n"
        "FAIL human-equivalent full_user_e2e contract missing: docs/FULL_USER_E2E.md"
    )


def test_checkout_failure_continues_inventory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        good = base / "good"
        good.mkdir()
        init_repo(good)
        adopt_python(good, BASELINE)
        inventory = base / "inventory.json"
        write_inventory(
            inventory,
            [
                {
                    "full_name": "demo/missing",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(base / "does-not-exist"),
                },
                {
                    "full_name": "demo/good",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(good),
                },
            ],
        )
        audit = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(inventory),
            "--audit",
            "--baseline-sha",
            BASELINE,
            check=False,
        )
        assert audit.returncode != 0
        assert "REPO=demo/missing" in audit.stdout
        assert "STATE=ERROR" in audit.stdout
        assert "OUTCOME=FAIL" in audit.stdout
        assert "REPO=demo/good" in audit.stdout
        assert "STATE=CURRENT" in audit.stdout
        assert "ORG_ROLLOUT=FAIL" in audit.stdout


def test_override_manifest_exclude_and_adopt() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        excluded = base / "excluded"
        invalid_review = base / "invalid-review"
        adoptable = base / "adoptable"
        for path in (excluded, invalid_review, adoptable):
            path.mkdir()
            init_repo(path)
            (path / "README.md").write_text("x\n", encoding="utf-8")
            commit_all(path)

        inventory = base / "inventory.json"
        write_inventory(
            inventory,
            [
                {
                    "full_name": "demo/excluded",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(excluded),
                },
                {
                    "full_name": "demo/invalid-review",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(invalid_review),
                },
                {
                    "full_name": "demo/adoptable",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(adoptable),
                },
            ],
        )
        manifest = base / "overrides.yaml"
        manifest.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "defaults": {"ack_rule_review": True, "allow_no_tests": True, "ci_mode": "shared"},
                    "repositories": {
                        "demo/excluded": {"exclude": True},
                        "demo/invalid-review": {"user_gate_contracts_reviewed": True},
                        "demo/adoptable": {"test_command": "true"},
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        audited = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(inventory),
            "--override-manifest",
            str(manifest),
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        audit_invalid = audited.stdout.split("REPO=demo/invalid-review", 1)[1].split("---", 1)[0]
        assert "ACTION=NEEDS_INPUT" in audit_invalid
        assert "OUTCOME=NEEDS_INPUT" in audit_invalid
        audit_adoptable = audited.stdout.split("REPO=demo/adoptable", 1)[1].split("---", 1)[0]
        assert "OUTCOME=PLANNED" in audit_adoptable

        applied = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(inventory),
            "--override-manifest",
            str(manifest),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert "REPO=demo/excluded" in applied.stdout
        assert "STATE=EXCLUDED" in applied.stdout
        assert "REPO=demo/invalid-review" in applied.stdout
        invalid_segment = applied.stdout.split("REPO=demo/invalid-review", 1)[1].split("---", 1)[0]
        assert "ACTION=NEEDS_INPUT" in invalid_segment
        assert "OUTCOME=NEEDS_INPUT" in invalid_segment
        assert not (invalid_review / ".engineering").exists()
        assert "REPO=demo/adoptable" in applied.stdout
        adopt_segment = applied.stdout.split("REPO=demo/adoptable", 1)[1].split("---", 1)[0]
        assert "OUTCOME=APPLIED" in adopt_segment
        assert (adoptable / ".engineering" / "project.yaml").is_file()
        project = load_yaml(adoptable / ".engineering" / "project.yaml")
        assert project["engineering_system"]["version"] == "1.7.0"
        assert project["engineering_system"]["baseline"] == NEW_BASELINE


def test_attestation_repair_classification() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "review-provenance"
        root.mkdir()
        init_repo(root)
        adopt_user_facing(root, BASELINE)

        release_path = root / ".engineering/release.yaml"
        release = load_yaml(release_path)
        release["human_equivalent_user_tests"].pop(
            "contract_review_attestation_version", None
        )
        release_path.write_text(
            yaml.safe_dump(release, sort_keys=False), encoding="utf-8"
        )
        commit_all(root, "remove review attestation")

        missing = org_rollout.classify_checkout(root, "1.7.0", BASELINE)
        assert missing.state == "OUTDATED", missing
        assert missing.action == "UPGRADE", missing
        repaired = org_rollout.apply_upgrade(
            root, BASELINE, {"user_gate_contracts_reviewed": True}
        )
        assert repaired.returncode == 0, repaired.stdout
        commit_all(root, "repair review attestation")

        for malformed in (True, 1.0):
            bad = load_yaml(release_path)
            bad["human_equivalent_user_tests"][
                "contract_review_attestation_version"
            ] = malformed
            release_path.write_text(
                yaml.safe_dump(bad, sort_keys=False), encoding="utf-8"
            )
            commit_all(root, f"malformed review attestation {malformed!r}")
            classified = org_rollout.classify_checkout(root, "1.7.0", BASELINE)
            assert classified.state == "INCOMPLETE", classified
            assert classified.action == "NEEDS_INPUT", classified

            fixed = load_yaml(release_path)
            fixed["human_equivalent_user_tests"][
                "contract_review_attestation_version"
            ] = 1
            release_path.write_text(
                yaml.safe_dump(fixed, sort_keys=False), encoding="utf-8"
            )
            commit_all(root, "restore valid review attestation")


def test_user_gate_contract_review_override_propagates() -> None:
    root = Path("/tmp/example-user-facing")
    override = {
        "user_facing": True,
        "user_gate_contracts_reviewed": True,
        "primary_user_surface": "browser",
        "surface_reconciliation_contract": "docs/SURFACE_RECONCILIATION.md",
        "full_user_e2e_contract": "docs/FULL_USER_E2E.md",
    }
    adopt_argv = org_rollout.build_adopt_argv(root, NEW_BASELINE, override)
    upgrade_argv = org_rollout.build_upgrade_argv(root, NEW_BASELINE, override)
    assert "--user-facing" in adopt_argv
    assert "--user-gate-contracts-reviewed" in adopt_argv
    assert "--primary-user-surface" in adopt_argv
    assert "browser" in adopt_argv
    assert "--surface-reconciliation-contract" in adopt_argv
    assert "docs/SURFACE_RECONCILIATION.md" in adopt_argv
    assert "--full-user-e2e-contract" in adopt_argv
    assert "docs/FULL_USER_E2E.md" in adopt_argv
    assert "--user-gate-contracts-reviewed" in upgrade_argv

    try:
        org_rollout.build_adopt_argv(
            root, NEW_BASELINE, {"user_gate_contracts_reviewed": True}
        )
    except SystemExit as exc:
        assert "requires repository override user_facing: true" in str(exc)
    else:
        raise AssertionError("review acknowledgement must not silently adopt as non-user-facing")

    for no_review in ({}, {"user_gate_contracts_reviewed": False}, {"user_gate_contracts_reviewed": "false"}):
        assert "--user-gate-contracts-reviewed" not in org_rollout.build_adopt_argv(
            root, NEW_BASELINE, no_review
        )
        assert "--user-gate-contracts-reviewed" not in org_rollout.build_upgrade_argv(
            root, NEW_BASELINE, no_review
        )

    with tempfile.TemporaryDirectory() as tmp:
        manifest = Path(tmp) / "overrides.yaml"
        manifest.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "defaults": {"user_gate_contracts_reviewed": True},
                    "repositories": {},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        try:
            org_rollout.load_override_manifest(manifest)
        except SystemExit as exc:
            assert "forbidden in override defaults" in str(exc)
        else:
            raise AssertionError("defaults review acknowledgement must fail closed")

        manifest.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "defaults": {},
                    "repositories": {
                        "demo/repo": {"user_gate_contracts_reviewed": "false"}
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        try:
            org_rollout.load_override_manifest(manifest)
        except SystemExit as exc:
            assert "must be a literal boolean" in str(exc)
        else:
            raise AssertionError("non-boolean review acknowledgement must fail closed")

        manifest.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "defaults": {"ci_mode": "shared"},
                    "repositories": {
                        "demo/reviewed": {"user_gate_contracts_reviewed": True},
                        "demo/unreviewed": {},
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        loaded = org_rollout.load_override_manifest(manifest)
        assert loaded.for_repo("demo/reviewed")["user_gate_contracts_reviewed"] is True
        assert "user_gate_contracts_reviewed" not in loaded.for_repo("demo/unreviewed")


def main() -> int:
    run(sys.executable, "-m", "py_compile", str(ROLLOUT), str(ADOPT), str(UPGRADE))
    test_flatten_paginated_inventory()
    test_org_rollout_matrix()
    test_same_version_different_baseline_is_outdated()
    test_incomplete_surfaces_not_reported_current()
    test_current_baseline_with_stale_execution_policy_is_repairable()
    test_current_baseline_with_managed_byte_drift_is_repairable()
    test_repair_markers_match_checker_diagnostics()
    test_checkout_failure_continues_inventory()
    test_override_manifest_exclude_and_adopt()
    test_attestation_repair_classification()
    test_user_gate_contract_review_override_propagates()
    print("ORG_ROLLOUT_TOOL_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
