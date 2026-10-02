#!/usr/bin/env python3
"""Regression tests for the base-owned governance floor."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "governance_floor", HERE / "governance_floor.py"
)
assert spec and spec.loader
floor = importlib.util.module_from_spec(spec)
sys.modules["governance_floor"] = floor
spec.loader.exec_module(floor)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


def write_managed(root: Path, epoch: int, baseline: str = "a" * 40) -> None:
    (root / ".engineering").mkdir(parents=True, exist_ok=True)
    (root / ".github/workflows").mkdir(parents=True, exist_ok=True)
    (root / "tools").mkdir(parents=True, exist_ok=True)
    (root / "schemas").mkdir(parents=True, exist_ok=True)
    (root / ".engineering/project.yaml").write_text(
        f"engineering_system:\n"
        f"  version: 1.7.0\n"
        f"  policy_epoch: {epoch}\n"
        f"  mode: adopted\n"
        f"  baseline: {baseline}\n"
        f"  ci_mode: shared\n",
        encoding="utf-8",
    )
    (root / ".engineering/requirements-engineering-system.txt").write_text(
        "PyYAML==6.0.2\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        "Execution profile authority: runtime selection comes from "
        ".engineering/execution-profile.yaml.\n"
        "Execution authority precedence: current owner and ACTIVE Work Packet.\n",
        encoding="utf-8",
    )
    (root / ".engineering/execution-profile.yaml").write_text(
        (HERE.parent / ".engineering/execution-profile.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "tools/execution_profile.py").write_text(
        (HERE / "execution_profile.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "schemas/execution-profile.schema.json").write_text(
        (HERE.parent / "schemas/execution-profile.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "tools/context_epoch.py").write_text(
        "from execution_profile import load_profile, packet_authority\n"
        "EXECUTION_PROFILE_REVISION = True\n",
        encoding="utf-8",
    )
    (root / "tools/engineering-context.py").write_text(
        'MANDATORY_CONTEXT_PATHS = {"AGENTS.md", ".engineering/project.yaml"}\n',
        encoding="utf-8",
    )
    source = (HERE / "governance_floor.py").read_text(encoding="utf-8")
    (root / "tools/governance_floor.py").write_text(source, encoding="utf-8")
    for rel in ("tools/adopt.py", "tools/check-adoption.py", "tools/upgrade-adoption.py"):
        (root / rel).write_text((HERE.parent / rel).read_text(encoding="utf-8"), encoding="utf-8")
    (root / ".github/workflows/adoption-compliance.yml").write_text(
        (HERE.parent / ".github/workflows/adoption-compliance.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / ".github/workflows/engineering-system.yml").write_text(
        "name: Engineering System\n"
        "on:\n"
        "  pull_request:\n"
        "  pull_request_target:\n"
        "permissions:\n"
        "  contents: read\n"
        "jobs:\n"
        "  governance-floor:\n"
        "    if: github.event_name == 'pull_request_target'\n"
        "    uses: datarelay-labs/engineering-system/.github/workflows/"
        "governance-floor.yml@" + baseline + "\n"
        "    with:\n"
        "      base_sha: ${{ github.event.pull_request.base.sha }}\n"
        "      head_sha: ${{ github.event.pull_request.head.sha }}\n"
        "  adoption-compliance:\n"
        "    if: github.event_name == 'pull_request'\n"
        "    uses: datarelay-labs/engineering-system/.github/workflows/"
        "adoption-compliance.yml@" + baseline + "\n"
        "  enforcement-reconcile:\n"
        "    if: github.event_name == 'pull_request'\n"
        "    uses: datarelay-labs/engineering-system/.github/workflows/"
        "enforcement-check.yml@" + baseline + "\n"
        "  affected-tests:\n"
        "    if: github.event_name == 'pull_request'\n"
        "    uses: datarelay-labs/engineering-system/.github/workflows/"
        "affected-tests.yml@" + baseline + "\n"
        "    with:\n"
        "      manifest_path: .engineering/tests.yaml\n"
        "      trigger: pr\n",
        encoding="utf-8",
    )


def write_root_migration(
    root: Path,
    *,
    base: str,
    from_epoch: int,
    to_epoch: int,
    paths: list[str],
) -> None:
    entries = [
        {"path": path, "head_blob_sha": git(root, "hash-object", path)}
        for path in sorted(paths)
    ]
    payload = {
        "contract_version": 1,
        "base_sha": base,
        "from_policy_epoch": from_epoch,
        "to_policy_epoch": to_epoch,
        "requires_exact_head_validate": True,
        "automation_eligible": False,
        "rationale": "test root-of-trust migration",
        "changed_surfaces": entries,
    }
    (root / ".engineering/governance-migration.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def fixture(root: Path) -> str:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Governance Floor Test")
    write_managed(root, 1)
    return commit(root, "base")


def prebridge_fixture(root: Path) -> str:
    fixture(root)
    for rel in floor.EXECUTION_PROFILE_SURFACES:
        (root / rel).unlink()
    (root / "AGENTS.md").write_text(
        "Execution authority precedence: current owner and ACTIVE Work Packet.\n"
        "IMPLEMENTER=CHATGPT_CHAT\n",
        encoding="utf-8",
    )
    (root / "tools/context_epoch.py").write_text(
        'if implementer and implementer != "CHATGPT_CHAT":\n'
        '    blocking.append("IMPLEMENTER_INVALID")\n',
        encoding="utf-8",
    )
    return commit(root, "pre-bridge base")


def stage_a_bridge_fixture(root: Path) -> str:
    prebridge_fixture(root)
    project_path = root / ".engineering/project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["engineering_system"]["policy_epoch"] = 2
    project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    stage_base = commit(root, "stage-a pre-bridge epoch-2 base")

    install_execution_profile_fixture(root, authority_contract="legacy-v2")
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["engineering_system"]["policy_epoch"] = 3
    project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    helper = root / floor.GOVERNANCE_HELPER
    helper.write_text(
        helper.read_text(encoding="utf-8") + "\n# stage-a bridge fixture\n",
        encoding="utf-8",
    )
    write_root_migration(
        root,
        base=stage_base,
        from_epoch=2,
        to_epoch=3,
        paths=[floor.GOVERNANCE_HELPER],
    )
    return commit(root, "stage-a legacy-v2 bridge")


def test_safe_head_passes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = fixture(root)
        (root / "README.md").write_text("product change\n", encoding="utf-8")
        head = commit(root, "product")
        status, reasons, base_epoch, head_epoch = floor.evaluate(root, base, head)
        assert status == "PASS", reasons
        assert reasons == []
        assert (base_epoch, head_epoch) == (1, 1)


def test_policy_epoch_regression_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = fixture(root)
        write_managed(root, 0)
        head = commit(root, "regress epoch")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "BLOCK"
        assert any("GOVERNANCE_POLICY_EPOCH_REGRESSION" in item for item in reasons)


def test_execution_surface_accepts_profile_neutral_prose() -> None:
    profile = floor.load_profile_text(
        (HERE.parent / ".engineering/execution-profile.yaml").read_text(encoding="utf-8")
    )
    content = (
        "Execution profile authority: runtime selection comes from "
        ".engineering/execution-profile.yaml.\n"
        "Execution authority precedence: current owner, then current ACTIVE Work Packet.\n"
    )
    assert floor._execution_surface_reasons("AGENTS.md", content, profile) == []


def test_retired_implementer_and_artifact_block() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = fixture(root)
        (root / "AGENTS.md").write_text(
            "Cursor is disabled by default and the owner explicitly reactivates it "
            "for the current Work Packet with `IMPLEMENTER=CURSOR`.\n",
            encoding="utf-8",
        )
        (root / ".cursor").mkdir()
        (root / ".cursor/rule").write_text("retired\n", encoding="utf-8")
        head = commit(root, "retired")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "BLOCK"
        assert "RETIRED_RUNTIME_REINTRODUCED:AGENTS.md" in reasons
        assert "RETIRED_RUNTIME_ARTIFACT_REINTRODUCED:.cursor" in reasons


def test_old_context_epoch_allowlist_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = fixture(root)
        (root / "tools/context_epoch.py").write_text(
            'if implementer and implementer not in {"CHATGPT_CHAT", "CURSOR"}:\n'
            '    blocking.append("IMPLEMENTER_INVALID")\n',
            encoding="utf-8",
        )
        head = commit(root, "old implementer validator")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "BLOCK"
        assert "PROVIDER_RUNTIME_COUPLING:tools/context_epoch.py" in reasons


def test_epoch_advance_cannot_remove_profile_authority_guard() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = fixture(root)
        write_managed(root, 2)
        (root / "tools/context_epoch.py").write_text(
            "blocking = []\n",
            encoding="utf-8",
        )
        head = commit(root, "weaken implementer guard")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "BLOCK"
        assert any(
            item.startswith(
                "MANAGED_EXECUTION_INVARIANT_MISSING:tools/context_epoch.py:"
            )
            for item in reasons
        )


def test_workflow_comment_tokens_do_not_preserve_floor() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = fixture(root)
        write_managed(root, 2)
        (root / ".github/workflows/engineering-system.yml").write_text(
            "name: Engineering System\n"
            "on:\n"
            "  pull_request:\n"
            "# pull_request_target:\n"
            "# governance-floor:\n"
            "# github.event_name == 'pull_request_target'\n"
            "# datarelay-labs/engineering-system/.github/workflows/governance-floor.yml@"
            + ("b" * 40)
            + "\n"
            "# github.event.pull_request.base.sha\n"
            "# github.event.pull_request.head.sha\n",
            encoding="utf-8",
        )
        head = commit(root, "comment-only floor")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "BLOCK"
        assert "GOVERNANCE_WORKFLOW_TRIGGER_MISSING:pull_request_target" in reasons


def test_filtered_pull_request_target_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = fixture(root)
        write_managed(root, 2)
        (root / ".github/workflows/engineering-system.yml").write_text(
            "name: Engineering System\n"
            "on:\n"
            "  pull_request:\n"
            "  pull_request_target:\n"
            "    branches:\n"
            "      - never-matches\n"
            "jobs:\n"
            "  governance-floor:\n"
            "    if: github.event_name == 'pull_request_target'\n"
            "    uses: datarelay-labs/engineering-system/.github/workflows/"
            "governance-floor.yml@" + ("a" * 40) + "\n"
            "    with:\n"
            "      base_sha: ${{ github.event.pull_request.base.sha }}\n"
            "      head_sha: ${{ github.event.pull_request.head.sha }}\n",
            encoding="utf-8",
        )
        head = commit(root, "filtered governance trigger")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "BLOCK"
        assert "GOVERNANCE_WORKFLOW_TRIGGER_FILTERED:pull_request_target" in reasons


def test_unrelated_database_cursor_language_is_allowed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = fixture(root)
        agents = root / "AGENTS.md"
        agents.write_text(
            agents.read_text(encoding="utf-8")
            + "Use a database cursor for bounded row iteration.\n",
            encoding="utf-8",
        )
        head = commit(root, "database cursor guidance")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "PASS", reasons


def test_guard_change_requires_policy_epoch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = fixture(root)
        helper = root / "tools/governance_floor.py"
        helper.write_text(helper.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
        head = commit(root, "guard drift")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "BLOCK"
        assert (
            "GOVERNANCE_SURFACE_CHANGED_WITHOUT_POLICY_EPOCH:"
            "tools/governance_floor.py"
        ) in reasons

        write_managed(root, 2)
        helper = root / "tools/governance_floor.py"
        helper.write_text(helper.read_text(encoding="utf-8") + "\n# epoch-2\n", encoding="utf-8")
        head2 = commit(root, "policy upgrade without migration evidence")
        status2, reasons2, _, _ = floor.evaluate(root, base, head2)
        assert status2 == "BLOCK"
        assert "GOVERNANCE_ROOT_MIGRATION_MANIFEST_MISSING" in reasons2

        write_root_migration(
            root,
            base=base,
            from_epoch=1,
            to_epoch=2,
            paths=["tools/governance_floor.py"],
        )
        head3 = commit(root, "policy upgrade with migration evidence")
        status3, reasons3, _, _ = floor.evaluate(root, base, head3)
        assert status3 == "PASS", reasons3


def test_workflow_self_preservation_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = fixture(root)
        (root / ".github/workflows/engineering-system.yml").write_text(
            "name: Engineering System\non:\n  pull_request:\n",
            encoding="utf-8",
        )
        head = commit(root, "remove floor")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "BLOCK"
        assert "GOVERNANCE_WORKFLOW_TRIGGER_MISSING:pull_request_target" in reasons


def test_managed_pr_jobs_cannot_be_removed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = fixture(root)
        write_managed(root, 2)
        baseline = "a" * 40
        (root / ".github/workflows/engineering-system.yml").write_text(
            "name: Engineering System\n"
            "on:\n"
            "  pull_request:\n"
            "  pull_request_target:\n"
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  governance-floor:\n"
            "    if: github.event_name == 'pull_request_target'\n"
            "    uses: datarelay-labs/engineering-system/.github/workflows/"
            "governance-floor.yml@" + baseline + "\n"
            "    with:\n"
            "      base_sha: ${{ github.event.pull_request.base.sha }}\n"
            "      head_sha: ${{ github.event.pull_request.head.sha }}\n",
            encoding="utf-8",
        )
        head = commit(root, "remove managed validation jobs")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "BLOCK"
        assert "GOVERNANCE_MANAGED_JOB_MISSING:adoption-compliance" in reasons
        assert "GOVERNANCE_MANAGED_JOB_MISSING:enforcement-reconcile" in reasons
        assert "GOVERNANCE_MANAGED_JOB_MISSING:affected-tests" in reasons
        assert "GOVERNANCE_MANAGED_JOB_SET_INVALID" in reasons


def test_dependency_manifest_requires_policy_epoch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = fixture(root)
        (root / ".engineering/requirements-engineering-system.txt").write_text(
            "PyYAML==6.0.3\n",
            encoding="utf-8",
        )
        head = commit(root, "change governance dependency")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "BLOCK"
        assert (
            "GOVERNANCE_SURFACE_CHANGED_WITHOUT_POLICY_EPOCH:"
            ".engineering/requirements-engineering-system.txt"
        ) in reasons

        write_managed(root, 2)
        (root / ".engineering/requirements-engineering-system.txt").write_text(
            "PyYAML==6.0.3\n",
            encoding="utf-8",
        )
        write_root_migration(
            root,
            base=base,
            from_epoch=1,
            to_epoch=2,
            paths=[".engineering/requirements-engineering-system.txt"],
        )
        head2 = commit(root, "change governance dependency with migration evidence")
        status2, reasons2, _, _ = floor.evaluate(root, base, head2)
        assert status2 == "PASS", reasons2


def test_canonical_floor_change_requires_policy_epoch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixture(root)
        project_path = root / ".engineering/project.yaml"
        project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
        project["engineering_system"]["mode"] = "canonical"
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        for rel in (
            ".github/workflows/governance-floor.yml",
            ".github/workflows/validate.yml",
        ):
            source = HERE.parent / rel
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        base = commit(root, "canonical base")

        floor_workflow = root / ".github/workflows/governance-floor.yml"
        floor_workflow.write_text(
            floor_workflow.read_text(encoding="utf-8") + "\n# same-epoch drift\n",
            encoding="utf-8",
        )
        head = commit(root, "canonical floor drift")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "BLOCK"
        assert (
            "GOVERNANCE_SURFACE_CHANGED_WITHOUT_POLICY_EPOCH:"
            ".github/workflows/governance-floor.yml"
        ) in reasons

        project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
        project["engineering_system"]["policy_epoch"] = 2
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        write_root_migration(
            root,
            base=base,
            from_epoch=1,
            to_epoch=2,
            paths=[".github/workflows/governance-floor.yml"],
        )
        head2 = commit(root, "canonical floor migration")
        status2, reasons2, _, _ = floor.evaluate(root, base, head2)
        assert status2 == "PASS", reasons2


def test_canonical_workflows_have_direct_floor() -> None:
    root = HERE.parent
    floor_text = (root / ".github/workflows/governance-floor.yml").read_text(
        encoding="utf-8"
    )
    validate_text = (root / ".github/workflows/validate.yml").read_text(
        encoding="utf-8"
    )
    assert floor._canonical_floor_workflow_reasons(floor_text) == []
    assert floor._canonical_validate_reasons(validate_text) == []



def install_execution_profile_fixture(root: Path, *, authority_contract: str) -> None:
    source_root = HERE.parent
    for rel in (
        ".engineering/execution-profile.yaml",
        "tools/execution_profile.py",
        "schemas/execution-profile.schema.json",
    ):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((source_root / rel).read_text(encoding="utf-8"), encoding="utf-8")
    profile_path = root / ".engineering/execution-profile.yaml"
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    profile["authority_contract"] = authority_contract
    profile["revision"] = 1 if authority_contract == "legacy-v2" else 2
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")


def test_execution_profile_bootstrap_requires_legacy_contract() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = prebridge_fixture(root)
        project_path = root / ".engineering/project.yaml"
        project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
        project["engineering_system"]["policy_epoch"] = 2
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        install_execution_profile_fixture(root, authority_contract="legacy-v2")
        head = commit(root, "bootstrap legacy execution profile")
        status, reasons, _, _ = floor.evaluate(root, base, head)
        assert status == "PASS", reasons


def test_missing_execution_profile_helper_repair_uses_fallback() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        prebridge_fixture(root)
        project_path = root / ".engineering/project.yaml"
        project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
        project["engineering_system"]["policy_epoch"] = 2
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        install_execution_profile_fixture(root, authority_contract="legacy-v2")
        commit(root, "bridge base")
        (root / "tools/execution_profile.py").unlink()
        damaged_base = commit(root, "simulate missing execution profile helper")

        (root / "tools/execution_profile.py").write_text(
            (HERE / "execution_profile.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
        project["engineering_system"]["policy_epoch"] = 3
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        write_root_migration(
            root,
            base=damaged_base,
            from_epoch=2,
            to_epoch=3,
            paths=["tools/execution_profile.py"],
        )
        repaired_head = commit(root, "repair execution profile helper")
        git(root, "checkout", "-q", damaged_base)

        result = subprocess.run(
            [
                sys.executable,
                str(root / "tools/governance_floor.py"),
                "check",
                "--root",
                str(root),
                "--base-ref",
                damaged_base,
                "--head-ref",
                repaired_head,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "GOVERNANCE_FLOOR=PASS" in result.stdout


def test_execution_profile_v3_restore_requires_stage_a_and_exact_root_migration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stage_a_bridge_fixture(root)
        for rel in floor.EXECUTION_PROFILE_SURFACES:
            (root / rel).unlink()
        damaged_base = commit(root, "simulate missing post-bridge execution profile bundle")

        write_managed(root, 4)
        (root / floor.ROOT_MIGRATION_MANIFEST).unlink()
        unmanifested_head = commit(root, "restore profile-v3 without migration evidence")
        status, reasons, _, _ = floor.evaluate(root, damaged_base, unmanifested_head)
        assert status == "BLOCK"
        assert "GOVERNANCE_ROOT_MIGRATION_MANIFEST_MISSING" in reasons

        git(root, "reset", "--hard", damaged_base)
        write_managed(root, 4)
        changed_paths: list[str] = []
        for rel in floor.EPOCH_GUARDED_GOVERNANCE_SURFACES:
            before = floor._read_at(root, damaged_base, rel)
            target = root / rel
            after = target.read_text(encoding="utf-8") if target.is_file() else None
            if before != after:
                changed_paths.append(rel)
        write_root_migration(
            root,
            base=damaged_base,
            from_epoch=3,
            to_epoch=4,
            paths=changed_paths,
        )
        manifested_head = commit(root, "restore profile-v3 with migration evidence")
        status, reasons, _, _ = floor.evaluate(root, damaged_base, manifested_head)
        assert status == "PASS", reasons


def test_execution_profile_v3_restore_rejects_epoch_only_prebridge_base() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        prebridge_fixture(root)
        project_path = root / ".engineering/project.yaml"
        project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
        project["engineering_system"]["policy_epoch"] = 3
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        fake_epoch_base = commit(root, "pre-bridge base with copied epoch number")

        write_managed(root, 4)
        changed_paths: list[str] = []
        for rel in floor.EPOCH_GUARDED_GOVERNANCE_SURFACES:
            before = floor._read_at(root, fake_epoch_base, rel)
            target = root / rel
            after = target.read_text(encoding="utf-8") if target.is_file() else None
            if before != after:
                changed_paths.append(rel)
        write_root_migration(
            root,
            base=fake_epoch_base,
            from_epoch=3,
            to_epoch=4,
            paths=changed_paths,
        )
        direct_v3_head = commit(root, "attempt direct profile-v3 without stage-a bridge")
        status, reasons, _, _ = floor.evaluate(root, fake_epoch_base, direct_v3_head)
        assert status == "BLOCK"
        assert "EXECUTION_PROFILE_STAGE_A_EVIDENCE_MISSING" in reasons

def main() -> int:
    test_safe_head_passes()
    test_policy_epoch_regression_blocks()
    test_execution_surface_accepts_profile_neutral_prose()
    test_retired_implementer_and_artifact_block()
    test_old_context_epoch_allowlist_blocks()
    test_epoch_advance_cannot_remove_profile_authority_guard()
    test_workflow_comment_tokens_do_not_preserve_floor()
    test_filtered_pull_request_target_blocks()
    test_unrelated_database_cursor_language_is_allowed()
    test_guard_change_requires_policy_epoch()
    test_workflow_self_preservation_blocks()
    test_managed_pr_jobs_cannot_be_removed()
    test_dependency_manifest_requires_policy_epoch()
    test_canonical_floor_change_requires_policy_epoch()
    test_canonical_workflows_have_direct_floor()
    test_execution_profile_bootstrap_requires_legacy_contract()
    test_missing_execution_profile_helper_repair_uses_fallback()
    test_execution_profile_v3_restore_requires_stage_a_and_exact_root_migration()
    test_execution_profile_v3_restore_rejects_epoch_only_prebridge_base()
    print("GOVERNANCE_FLOOR_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
