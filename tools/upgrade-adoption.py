#!/usr/bin/env python3
"""Fail-closed managed upgrade helper for Engineering System adoption."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

from adopt import (
    KNOWLEDGE_CONTRACT_MANAGED,
    RESUME_ADAPTER_ALIASES,
    RUNTIME_CONTRACT_MANAGED,
    canonical_baseline,
    canonical_version,
    engineering_workflow,
    release_workflow,
)

CANONICAL = Path(__file__).resolve().parents[1]

# Known managed version/baseline declaration forms. Only these are rewritten;
# surrounding project-specific text is preserved. Ambiguous/custom forms fail closed.
KNOWN_BASELINE_DECLARATION_RES = (
    re.compile(
        r"(Adoption baseline: Engineering System version )"
        r"(\d+\.\d+\.\d+)"
        r"( at immutable commit `)"
        r"([0-9a-f]{40})"
        r"(`\.)"
    ),
    re.compile(
        r"(This canonical repository currently ships Engineering System )"
        r"(\d+\.\d+\.\d+)"
        r"(;)"
    ),
    re.compile(
        r"(canonical Engineering System release \(currently )"
        r"(\d+\.\d+\.\d+)"
        r"(\))"
    ),
    re.compile(
        r"(The current repository baseline identifies Engineering System \*\*)"
        r"(\d+\.\d+\.\d+)"
        r"(\*\*)"
    ),
    re.compile(
        r"(Engineering System )"
        r"(\d+\.\d+\.\d+)"
        r"( also reconciles)"
    ),
    re.compile(
        r"(Engineering System )"
        r"(\d+\.\d+\.\d+)"
        r"(은 )"
    ),
)

# Broad hints that look like Engineering System version/baseline pins.
# Any hint not fully covered by a known managed pattern is ambiguous.
BASELINE_DECLARATION_HINT_RES = (
    re.compile(r"Adoption baseline: Engineering System version \d+\.\d+\.\d+\b"),
    re.compile(r"This canonical repository currently ships Engineering System \d+\.\d+\.\d+\b"),
    re.compile(r"canonical Engineering System release \(currently \d+\.\d+\.\d+\)"),
    re.compile(r"The current repository baseline identifies Engineering System \*\*\d+\.\d+\.\d+\*\*"),
    re.compile(r"Engineering System \d+\.\d+\.\d+ also reconciles"),
    re.compile(r"Engineering System \d+\.\d+\.\d+은 "),
    re.compile(r"Engineering System version \d+\.\d+\.\d+\b"),
    re.compile(r"immutable commit `[0-9a-f]{40}`"),
    re.compile(r"engineering_system\.baseline[`'\"\s:=]+[0-9a-f]{40}"),
)


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


def known_managed_resume_texts(canonical_text: str) -> set[str]:
    """Return known managed resume adapter texts that may be safely replaced."""
    known = {canonical_text}
    history_dir = CANONICAL / "tools" / "managed_adapter_history" / "resume"
    if history_dir.is_dir():
        for path in sorted(history_dir.glob("*.md")):
            known.add(path.read_text(encoding="utf-8"))
    return known


def known_managed_cursor_rule_texts(canonical_text: str) -> set[str]:
    known = {canonical_text}
    history_dir = CANONICAL / "tools" / "managed_adapter_history" / "rule"
    if history_dir.is_dir():
        for path in sorted(history_dir.glob("*.mdc")):
            known.add(path.read_text(encoding="utf-8"))
    return known


def plan_cursor_rule_update(root: Path) -> str | None:
    source = CANONICAL / "templates" / ".cursor" / "rules" / "engineering-system.mdc"
    if not source.is_file():
        raise SystemExit("FAIL canonical Cursor engineering-system rule missing")
    canonical_text = source.read_text(encoding="utf-8")
    path = root / ".cursor/rules/engineering-system.mdc"
    if not path.is_file():
        return canonical_text
    existing = path.read_text(encoding="utf-8")
    if existing == canonical_text:
        return None
    if existing in known_managed_cursor_rule_texts(canonical_text):
        return canonical_text
    raise SystemExit(
        "FAIL .cursor/rules/engineering-system.mdc contains local/custom changes; "
        "preserve/review them manually before upgrade"
    )


def apply_cursor_rule_update(root: Path, planned_text: str | None) -> bool:
    if planned_text is None:
        return False
    path = root / ".cursor/rules/engineering-system.mdc"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(planned_text, encoding="utf-8")
    return True


def plan_cursorignore_install(root: Path) -> str | None:
    path = root / ".cursorignore"
    if path.exists():
        return None
    source = CANONICAL / "templates" / ".cursorignore"
    if not source.is_file():
        raise SystemExit("FAIL canonical .cursorignore template missing")
    return source.read_text(encoding="utf-8")


def apply_cursorignore_install(root: Path, planned_text: str | None) -> bool:
    if planned_text is None:
        return False
    (root / ".cursorignore").write_text(planned_text, encoding="utf-8")
    return True


def plan_cursor_resume_adapters(root: Path) -> dict[str, str]:
    """Validate managed Cursor resume adapters before any repository mutation."""
    source = CANONICAL / "templates" / ".cursor" / "commands" / "resume.md"
    if not source.is_file():
        raise SystemExit("FAIL canonical Cursor resume template missing")
    text = source.read_text(encoding="utf-8")
    known = known_managed_resume_texts(text)
    planned: dict[str, str] = {}

    resume_path = root / ".cursor/commands/resume.md"
    if not resume_path.is_file():
        planned[".cursor/commands/resume.md"] = text
    else:
        existing = resume_path.read_text(encoding="utf-8")
        if existing == text:
            pass
        elif existing in known:
            planned[".cursor/commands/resume.md"] = text
        else:
            raise SystemExit(
                "FAIL .cursor/commands/resume.md contains local/custom changes; "
                "preserve/review them manually before upgrade"
            )

    for rel in RESUME_ADAPTER_ALIASES:
        path = root / rel
        if not path.is_file():
            continue
        existing = path.read_text(encoding="utf-8")
        if existing == text:
            continue
        if existing in known:
            planned[rel] = text
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before upgrade"
        )
    return planned


def apply_cursor_resume_adapters(root: Path, planned: dict[str, str]) -> list[str]:
    updated: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        updated.append(rel)
    return updated


def sync_cursor_resume_adapters(root: Path) -> list[str]:
    """Compatibility wrapper for callers outside the upgrade transaction."""
    return apply_cursor_resume_adapters(root, plan_cursor_resume_adapters(root))


def plan_knowledge_contract_install(root: Path) -> dict[str, str]:
    """Install the optional knowledge-contract helper only when the path is missing.

    Identical canonical copies are left unchanged. A different existing file fails
    closed before any upgrade mutation. `.engineering/knowledge.yaml` is not created.
    """
    planned: dict[str, str] = {}
    for rel in KNOWLEDGE_CONTRACT_MANAGED:
        source = CANONICAL / rel
        if not source.is_file():
            raise SystemExit(f"FAIL canonical {rel} missing")
        text = source.read_text(encoding="utf-8")
        path = root / rel
        if not path.exists():
            planned[rel] = text
            continue
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before upgrade"
        )
    return planned


def apply_knowledge_contract_install(root: Path, planned: dict[str, str]) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed


def plan_runtime_contract_install(root: Path) -> dict[str, str]:
    """Install the optional runtime-contract helper only when the path is missing.

    Identical canonical copies are left unchanged. A different existing file fails
    closed before any upgrade mutation. `.engineering/runtime.yaml` is not created,
    and health, smoke, and E2E commands are not rewritten by this helper.
    """
    planned: dict[str, str] = {}
    for rel in RUNTIME_CONTRACT_MANAGED:
        source = CANONICAL / rel
        if not source.is_file():
            raise SystemExit(f"FAIL canonical {rel} missing")
        text = source.read_text(encoding="utf-8")
        path = root / rel
        if not path.exists():
            planned[rel] = text
            continue
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before upgrade"
        )
    return planned


def apply_runtime_contract_install(root: Path, planned: dict[str, str]) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed

def _span_covered(span: tuple[int, int], covered: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start >= c_start and end <= c_end for c_start, c_end in covered)


def rewrite_known_baseline_declarations(
    text: str, new_version: str, new_baseline: str
) -> tuple[str, bool]:
    """Rewrite known managed version/baseline declarations; fail closed on ambiguity.

    Returns (new_text, changed). Raises SystemExit when a declaration-like hint is
    present but is not an exact known managed form.
    """
    covered: list[tuple[int, int]] = []
    for pattern in KNOWN_BASELINE_DECLARATION_RES:
        for match in pattern.finditer(text):
            covered.append(match.span())

    for pattern in BASELINE_DECLARATION_HINT_RES:
        for match in pattern.finditer(text):
            if not _span_covered(match.span(), covered):
                raise SystemExit(
                    "FAIL AGENTS.md/README contains ambiguous/custom Engineering System "
                    "version/baseline declarations; preserve/review them manually before upgrade"
                )

    updated = text
    for pattern in KNOWN_BASELINE_DECLARATION_RES:
        def _replace(match: re.Match[str], _pattern: re.Pattern[str] = pattern) -> str:
            groups = list(match.groups())
            # Patterns alternate literal, version, literal, optional sha, optional literal.
            if len(groups) == 5 and re.fullmatch(r"[0-9a-f]{40}", groups[3] or ""):
                groups[1] = new_version
                groups[3] = new_baseline
            elif len(groups) >= 2:
                groups[1] = new_version
            return "".join(groups)

        updated = pattern.sub(_replace, updated)

    return updated, updated != text


def plan_baseline_declaration_updates(
    root: Path, old_version: str, old_baseline: str, new_version: str, new_baseline: str
) -> list[tuple[str, str]]:
    """Compute rewrite + stale validation for AGENTS.md/README before any mutation.

    Returns (rel, rewritten_text) pairs for files that would change. Raises SystemExit
    on ambiguous/custom forms or remaining stale version/baseline substrings.
    """
    planned: list[tuple[str, str]] = []
    for rel in ("AGENTS.md", "README.md"):
        path = root / rel
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        try:
            rewritten, changed = rewrite_known_baseline_declarations(
                original, new_version, new_baseline
            )
        except SystemExit as exc:
            message = str(exc)
            if message.startswith("FAIL AGENTS.md/README"):
                raise SystemExit(message.replace("AGENTS.md/README", rel, 1)) from exc
            raise
        if old_version and old_version != new_version and old_version in rewritten:
            raise SystemExit(
                f"FAIL {rel} still contains stale Engineering System version "
                f"{old_version} after managed declaration sync; review manually"
            )
        if old_baseline and old_baseline != new_baseline and old_baseline in rewritten:
            raise SystemExit(
                f"FAIL {rel} still contains stale Engineering System baseline "
                f"{old_baseline} after managed declaration sync; review manually"
            )
        if changed:
            planned.append((rel, rewritten))
    return planned


def apply_baseline_declaration_updates(root: Path, planned: list[tuple[str, str]]) -> list[str]:
    """Write previously validated declaration rewrites."""
    updated: list[str] = []
    for rel, rewritten in planned:
        (root / rel).write_text(rewritten, encoding="utf-8")
        updated.append(rel)
    return updated


def sync_baseline_declarations(
    root: Path, old_version: str, old_baseline: str, new_version: str, new_baseline: str
) -> list[str]:
    """Synchronize known managed AGENTS.md/README version+baseline declarations."""
    planned = plan_baseline_declaration_updates(
        root, old_version, old_baseline, new_version, new_baseline
    )
    return apply_baseline_declaration_updates(root, planned)


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
    if semver_tuple(old_version) < (1, 5, 0):
        raise SystemExit(
            "FAIL automatic upgrade currently supports managed Engineering System 1.5+; "
            "older adoptions require an explicit intermediate review"
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

    # Validate managed declaration rewrites AND stale old-version/old-baseline
    # checks for both files before mutating metadata/workflows/adapters.
    planned_declarations = plan_baseline_declaration_updates(
        root, old_version, old_baseline, current_version, new_baseline
    )
    planned_cursor_rule = plan_cursor_rule_update(root)
    planned_cursorignore = plan_cursorignore_install(root)
    planned_resume_adapters = plan_cursor_resume_adapters(root)
    planned_knowledge_contract = plan_knowledge_contract_install(root)
    planned_runtime_contract = plan_runtime_contract_install(root)

    write_yaml(project_path, project)
    write_yaml(release_path, release)
    workflow_path.write_text(engineering_workflow(new_baseline, ci_mode), encoding="utf-8")

    if release_contract_enabled:
        release_workflow_path.write_text(release_workflow(new_baseline), encoding="utf-8")

    cursor_rule_updated = apply_cursor_rule_update(root, planned_cursor_rule)
    print("CURSOR_RULE_SYNCED=" + ("YES" if cursor_rule_updated else "NO"))

    cursorignore_installed = apply_cursorignore_install(root, planned_cursorignore)
    print("CURSORIGNORE_INSTALLED=" + ("YES" if cursorignore_installed else "NO"))

    synced_adapters = apply_cursor_resume_adapters(root, planned_resume_adapters)
    if synced_adapters:
        print("CURSOR_RESUME_ADAPTERS_SYNCED=" + ",".join(synced_adapters))
    else:
        print("CURSOR_RESUME_ADAPTERS_SYNCED=<none>")

    installed_knowledge = apply_knowledge_contract_install(root, planned_knowledge_contract)
    if installed_knowledge:
        print("KNOWLEDGE_CONTRACT_INSTALLED=" + ",".join(installed_knowledge))
    else:
        print("KNOWLEDGE_CONTRACT_INSTALLED=<none>")

    installed_runtime = apply_runtime_contract_install(root, planned_runtime_contract)
    if installed_runtime:
        print("RUNTIME_CONTRACT_INSTALLED=" + ",".join(installed_runtime))
    else:
        print("RUNTIME_CONTRACT_INSTALLED=<none>")

    synced_declarations = apply_baseline_declaration_updates(root, planned_declarations)
    if synced_declarations:
        print("BASELINE_DECLARATIONS_SYNCED=" + ",".join(synced_declarations))
    else:
        print("BASELINE_DECLARATIONS_SYNCED=<none>")

    checker = CANONICAL / "tools" / "check-adoption.py"
    result = subprocess.run([sys.executable, str(checker), "--root", str(root)])
    if result.returncode:
        raise SystemExit(result.returncode)

    print("ADOPTION_UPGRADE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
