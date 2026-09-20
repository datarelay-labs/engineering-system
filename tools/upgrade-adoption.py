#!/usr/bin/env python3
"""Fail-closed managed upgrade helper for Engineering System adoption."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

from adopt import canonical_baseline, canonical_version, engineering_workflow, release_workflow

CANONICAL = Path(__file__).resolve().parents[1]


def run_git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def write_yaml(path: Path, data: dict) -> None:
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def semver_tuple(value: str) -> tuple[int, int, int]:
    core = value.split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise SystemExit(f"FAIL invalid Engineering System version: {value}")
    return tuple(int(part) for part in parts)


def legacy_engineering_workflow(baseline: str, ci_mode: str) -> str:
    lines = [
        "name: Engineering System",
        "",
        "on:",
        "  pull_request:",
        "",
        "permissions:",
        "  contents: read",
        "",
        "jobs:",
        "  adoption-compliance:",
        f"    uses: datarelay-labs/engineering-system/.github/workflows/adoption-compliance.yml@{baseline}",
    ]
    if ci_mode == "shared":
        lines.extend(
            [
                "",
                "  affected-tests:",
                f"    uses: datarelay-labs/engineering-system/.github/workflows/affected-tests.yml@{baseline}",
                "    with:",
                "      manifest_path: .engineering/tests.yaml",
                "      trigger: pr",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def legacy_release_workflow(baseline: str, preflight_command: str, release_command: str) -> str:
    expression = "$" + "{{ inputs.expected_sha }}"
    lines = [
        "name: Engineering Release Qualification",
        "",
        "on:",
        "  workflow_dispatch:",
        "    inputs:",
        "      expected_sha:",
        "        description: Exact candidate SHA",
        "        required: true",
        "        type: string",
        "",
        "permissions:",
        "  contents: read",
        "",
        "jobs:",
    ]
    if preflight_command:
        lines.extend(
            [
                "  preflight:",
                f"    uses: datarelay-labs/engineering-system/.github/workflows/release-preflight.yml@{baseline}",
                "    with:",
                f"      expected_sha: {expression}",
                f"      preflight_command: {yaml.safe_dump(preflight_command).strip()}",
                "",
            ]
        )
    lines.append("  release-gate:")
    if preflight_command:
        lines.append("    needs: preflight")
    lines.extend(
        [
            f"    uses: datarelay-labs/engineering-system/.github/workflows/release-gate.yml@{baseline}",
            "    with:",
            f"      expected_sha: {expression}",
            f"      qualification_command: {yaml.safe_dump(release_command).strip()}",
            "",
        ]
    )
    return "\n".join(lines)


def coalesce(arg_value: str, current: object) -> str:
    value = arg_value.strip()
    if value:
        return value
    return str(current or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit or upgrade a managed Engineering System adoption")
    parser.add_argument("--root", required=True)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--baseline-sha", default="")
    parser.add_argument("--persistent-state", default="auto", choices=("auto", "yes", "no"))
    parser.add_argument("--runbook-path", action="append", default=[])
    parser.add_argument("--health-command", default="")
    parser.add_argument("--backup-command", default="")
    parser.add_argument("--restore-test-command", default="")
    parser.add_argument("--upgrade-command", default="")
    parser.add_argument("--rollback-command", default="")
    parser.add_argument("--release-setup-command", default="")
    parser.add_argument("--preflight-command", default="")
    parser.add_argument("--release-command", default="")
    parser.add_argument("--artifact-hash-command", default="")
    parser.add_argument("--provenance-command", default="")
    parser.add_argument("--sbom-command", default="")
    parser.add_argument("--operational-e2e-command", default="")
    parser.add_argument("--public-smoke-command", default="")
    parser.add_argument("--full-e2e-passes", type=int, default=-1)
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir() or not run_git(root, "rev-parse", "--show-toplevel"):
        raise SystemExit("FAIL target must be a Git repository")
    if run_git(root, "status", "--porcelain") and not args.allow_dirty:
        raise SystemExit("FAIL target worktree is dirty; preserve unrelated work before upgrade")

    project_path = root / ".engineering/project.yaml"
    release_path = root / ".engineering/release.yaml"
    workflow_path = root / ".github/workflows/engineering-system.yml"
    if not project_path.is_file() or not release_path.is_file() or not workflow_path.is_file():
        raise SystemExit("FAIL managed adoption metadata/workflow is incomplete; run adoption audit first")

    project = load_yaml(project_path)
    release = load_yaml(release_path)
    engineering = project.get("engineering_system") or {}
    operations = project.get("operations") or {}

    if engineering.get("mode") != "adopted":
        raise SystemExit("FAIL upgrade-adoption.py only upgrades engineering_system.mode=adopted repositories")

    old_version = str(engineering.get("version") or "")
    old_baseline = str(engineering.get("baseline") or "")
    current_version = canonical_version()
    new_baseline = canonical_baseline(args.baseline_sha)
    ci_mode = str(engineering.get("ci_mode") or "")
    if ci_mode not in {"shared", "native"}:
        raise SystemExit("FAIL existing adoption has invalid ci_mode")

    if semver_tuple(old_version) > semver_tuple(current_version):
        raise SystemExit(
            f"FAIL target adoption {old_version} is newer than canonical {current_version}"
        )

    if old_version == current_version and old_baseline == new_baseline:
        print("ADOPTION_UPGRADE=NO_CHANGE")
        return 0

    old_workflow = workflow_path.read_text(encoding="utf-8")
    safe_old_workflows = {
        legacy_engineering_workflow(old_baseline, ci_mode),
        engineering_workflow(old_baseline, ci_mode),
    }
    if old_workflow not in safe_old_workflows:
        raise SystemExit(
            "FAIL managed engineering-system.yml contains local/custom changes; preserve/review them manually before upgrade"
        )

    production = bool(operations.get("production_oriented"))
    existing_persistent = operations.get("persistent_state")
    if args.persistent_state == "yes":
        persistent_state = True
    elif args.persistent_state == "no":
        persistent_state = False
    elif isinstance(existing_persistent, bool):
        persistent_state = existing_persistent
    elif production:
        raise SystemExit(
            "FAIL production upgrade to 1.6 requires --persistent-state yes|no"
        )
    else:
        persistent_state = False

    runbook_paths = [item.strip() for item in args.runbook_path if item.strip()]
    if not runbook_paths:
        runbook_paths = [str(item) for item in (operations.get("runbook_paths") or []) if str(item).strip()]
    health_command = coalesce(args.health_command, operations.get("health_command"))
    backup_command = coalesce(args.backup_command, operations.get("backup_command"))
    restore_test_command = coalesce(args.restore_test_command, operations.get("restore_test_command"))
    upgrade_command = coalesce(args.upgrade_command, operations.get("upgrade_command"))
    rollback_command = coalesce(args.rollback_command, operations.get("rollback_command"))

    if production:
        if not runbook_paths:
            raise SystemExit("FAIL production upgrade to 1.6 requires --runbook-path")
        missing_runbooks = [rel for rel in runbook_paths if not (root / rel).is_file()]
        if missing_runbooks:
            raise SystemExit("FAIL production runbook path missing: " + ",".join(missing_runbooks))
        if not health_command:
            raise SystemExit("FAIL production upgrade to 1.6 requires --health-command")
    if persistent_state and (not backup_command or not restore_test_command):
        raise SystemExit(
            "FAIL persistent-state upgrade requires --backup-command and --restore-test-command"
        )

    setup_command = coalesce(args.release_setup_command, release.get("setup_command"))
    preflight_command = coalesce(args.preflight_command, release.get("preflight_command"))
    qualification_command = coalesce(args.release_command, release.get("qualification_command"))
    artifact_hash_command = coalesce(args.artifact_hash_command, release.get("artifact_hash_command"))
    provenance_command = coalesce(args.provenance_command, release.get("provenance_command"))
    sbom_command = coalesce(args.sbom_command, release.get("sbom_command"))
    operational_e2e_command = coalesce(args.operational_e2e_command, release.get("operational_e2e_command"))
    public_smoke_command = coalesce(args.public_smoke_command, release.get("public_smoke_command"))

    full_e2e_passes = args.full_e2e_passes
    if full_e2e_passes < 0:
        full_e2e_passes = int(release.get("full_e2e_passes") or (1 if production else 0))

    required_commands = (
        ("artifact_hash_required", artifact_hash_command),
        ("provenance_required", provenance_command),
        ("sbom_required", sbom_command),
    )
    for flag, value in required_commands:
        if bool(release.get(flag)) and not value:
            raise SystemExit(f"FAIL {flag}=true requires a corresponding 1.6 command")

    if production:
        if not operational_e2e_command:
            raise SystemExit("FAIL production upgrade to 1.6 requires --operational-e2e-command")
        if full_e2e_passes < 1:
            raise SystemExit("FAIL production upgrade requires full_e2e_passes>=1")
        if not public_smoke_command:
            raise SystemExit("FAIL production upgrade to 1.6 requires --public-smoke-command")

    plan = {
        "from_version": old_version,
        "to_version": current_version,
        "from_baseline": old_baseline,
        "to_baseline": new_baseline,
        "ci_mode": ci_mode,
        "production": production,
        "persistent_state": persistent_state,
        "release_contract": bool(
            qualification_command
            or setup_command
            or preflight_command
            or artifact_hash_command
            or provenance_command
            or sbom_command
            or operational_e2e_command
            or public_smoke_command
        ),
    }
    for key, value in plan.items():
        print(f"{key.upper()}={value}")

    if args.audit or not args.apply:
        print("ADOPTION_UPGRADE_AUDIT=PASS")
        if not args.apply:
            return 0

    engineering["version"] = current_version
    engineering["baseline"] = new_baseline
    project["engineering_system"] = engineering
    operations["persistent_state"] = persistent_state
    operations["runbook_paths"] = runbook_paths
    operations["health_command"] = health_command
    operations["backup_command"] = backup_command
    operations["restore_test_command"] = restore_test_command
    operations["upgrade_command"] = upgrade_command
    operations["rollback_command"] = rollback_command
    project["operations"] = operations

    release["setup_command"] = setup_command
    release["preflight_command"] = preflight_command
    release["preflight_required"] = bool(preflight_command)
    release["qualification_command"] = qualification_command
    release["artifact_hash_command"] = artifact_hash_command
    release["provenance_command"] = provenance_command
    release["sbom_command"] = sbom_command
    release["operational_e2e_command"] = operational_e2e_command
    release["public_smoke_command"] = public_smoke_command
    if production:
        release["operational_e2e_required"] = True
        release["full_e2e_passes"] = full_e2e_passes
        release["public_smoke_required"] = True

    release_workflow_path = root / ".github/workflows/engineering-release.yml"
    release_contract_enabled = bool(plan["release_contract"])
    if release_contract_enabled and release_workflow_path.is_file():
        old_release_workflow = release_workflow_path.read_text(encoding="utf-8")
        # Validate the managed release surface before mutating any repository files.
        if (
            f"release-preflight.yml@{old_baseline}" not in old_release_workflow
            and f"release-gate.yml@{old_baseline}" not in old_release_workflow
            and f"release-contract.yml@{old_baseline}" not in old_release_workflow
        ):
            raise SystemExit(
                "FAIL engineering-release.yml contains local/custom changes; review manually before upgrade"
            )

    write_yaml(project_path, project)
    write_yaml(release_path, release)
    workflow_path.write_text(engineering_workflow(new_baseline, ci_mode), encoding="utf-8")

    if release_contract_enabled:
        release_workflow_path.write_text(release_workflow(new_baseline), encoding="utf-8")

    checker = CANONICAL / "tools" / "check-adoption.py"
    result = subprocess.run([sys.executable, str(checker), "--root", str(root)])
    if result.returncode:
        raise SystemExit(result.returncode)

    print("ADOPTION_UPGRADE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
