#!/usr/bin/env python3
"""Fail-closed managed upgrade helper for Engineering System adoption."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from adopt import (
    CONTEXT_EPOCH_MANAGED,
    ENGINEERING_CONTEXT_MANAGED,
    ENGINEERING_SYSTEM_DEPENDENCIES_MANAGED,
    EXECUTION_PROFILE_MANAGED,
    GOVERNANCE_FLOOR_MANAGED,
    IMPLEMENTATION_PREFLIGHT_MANAGED,
    KNOWLEDGE_CONTRACT_MANAGED,
    POLICY_EPOCH,
    RUNTIME_CONTRACT_MANAGED,
    SKILLS_CONTRACT_MANAGED,
    TERMINAL_COMPLETION_NOTIFY_MANAGED,
    VERIFICATION_CONTRACT_MANAGED,
    WORK_PACKET_TEMPLATE_MANAGED,
    apply_execution_policy_sync,
    canonical_baseline,
    canonical_version,
    plan_execution_policy_sync,
    retired_agent_artifact_paths,
    engineering_workflow,
    release_workflow,
)

CANONICAL = Path(__file__).resolve().parents[1]
ROOT_MIGRATION_MANIFEST = ".engineering/governance-migration.yaml"

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


def pre_governance_floor_engineering_workflow(baseline: str, ci_mode: str) -> str:
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
        "",
        "  enforcement-reconcile:",
        f"    uses: datarelay-labs/engineering-system/.github/workflows/enforcement-check.yml@{baseline}",
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


def known_managed_file_hashes(rel: str, canonical_bytes: bytes, old_baseline: str = "") -> set[str]:
    """Return trusted hashes for current and historical adoption-managed files."""
    known = {hashlib.sha256(canonical_bytes).hexdigest()}
    if old_baseline and re.fullmatch(r"[0-9a-f]{40}", old_baseline):
        try:
            prior = subprocess.run(
                ["git", "-C", str(CANONICAL), "show", f"{old_baseline}:{rel}"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
            )
            if prior.returncode == 0:
                known.add(hashlib.sha256(prior.stdout).hexdigest())
            # Some adopted repositories were created from canonical managed bytes
            # whose formatting was normalized during adoption. Compare semantics
            # for JSON managed files before classifying those bytes as custom.
            if rel.endswith(".json") and prior.returncode == 0:
                try:
                    known_json = json.loads(prior.stdout.decode("utf-8"))
                    canonical_json = json.loads(canonical_bytes.decode("utf-8"))
                    if known_json == canonical_json:
                        pass
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
        except OSError:
            pass
    history_dir = CANONICAL / "tools" / "managed_adapter_history" / "file_hashes"
    if not history_dir.is_dir():
        return known
    for manifest in sorted(history_dir.glob("*.sha256")):
        for raw in manifest.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                raise SystemExit(f"FAIL malformed managed file hash manifest: {manifest}")
            digest, candidate_rel = parts[0], parts[1].strip()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise SystemExit(f"FAIL malformed managed file hash manifest digest: {manifest}")
            if candidate_rel == rel:
                known.add(digest)
    return known


def plan_managed_file_install(
    root: Path, managed: tuple[str, ...], *, label: str, old_baseline: str = ""
) -> dict[str, str]:
    """Plan updates only for current or cryptographically known managed bytes."""
    planned: dict[str, str] = {}
    for rel in managed:
        source = CANONICAL / rel
        if not source.is_file():
            raise SystemExit(f"FAIL canonical {rel} missing")
        canonical_bytes = source.read_bytes()
        canonical_text = canonical_bytes.decode("utf-8")
        path = root / rel
        if not path.exists():
            planned[rel] = canonical_text
            continue
        if not path.is_file():
            raise SystemExit(
                f"FAIL {rel} contains local/custom changes; preserve/review them manually before upgrade"
            )
        existing_bytes = path.read_bytes()
        if existing_bytes == canonical_bytes:
            continue
        digest = hashlib.sha256(existing_bytes).hexdigest()
        if digest in known_managed_file_hashes(rel, canonical_bytes, old_baseline):
            planned[rel] = canonical_text
            continue
        if rel.endswith(".json") and old_baseline and re.fullmatch(r"[0-9a-f]{40}", old_baseline):
            prior = subprocess.run(["git", "-C", str(CANONICAL), "show", f"{old_baseline}:{rel}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
            if prior.returncode == 0:
                try:
                    if json.loads(existing_bytes.decode("utf-8")) == json.loads(prior.stdout.decode("utf-8")):
                        planned[rel] = canonical_text
                        continue
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before upgrade"
        )
    return planned


def existing_retired_agent_artifacts(root: Path) -> list[str]:
    return [
        rel
        for rel in retired_agent_artifact_paths()
        if (root / rel).exists() or (root / rel).is_symlink()
    ]


def remove_retired_agent_artifacts(root: Path) -> list[str]:
    """Remove profile-declared retired runtime artifacts without following symlinks."""
    removed: list[str] = []
    for rel in retired_agent_artifact_paths():
        path = root / rel
        if path.is_symlink() or path.is_file():
            path.unlink()
            removed.append(rel)
        elif path.is_dir():
            shutil.rmtree(path)
            removed.append(rel)
        elif path.exists():
            path.unlink()
            removed.append(rel)
    return removed


def plan_work_packet_template_install(root: Path, old_baseline: str = "") -> dict[str, str]:
    """Install or upgrade only known managed Work Packet template bytes."""
    return plan_managed_file_install(
        root, WORK_PACKET_TEMPLATE_MANAGED, label="Work Packet template", old_baseline=old_baseline
    )


def apply_work_packet_template_install(root: Path, planned: dict[str, str]) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed


def plan_engineering_system_dependencies_install(root: Path, old_baseline: str = "") -> dict[str, str]:
    """Install the canonical Python dependency declaration for managed helpers."""
    return plan_managed_file_install(
        root, ENGINEERING_SYSTEM_DEPENDENCIES_MANAGED, label="Engineering System dependencies", old_baseline=old_baseline
    )


def apply_engineering_system_dependencies_install(
    root: Path, planned: dict[str, str]
) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed


def plan_knowledge_contract_install(root: Path, old_baseline: str = "") -> dict[str, str]:
    """Install or upgrade only known managed knowledge-contract bytes."""
    return plan_managed_file_install(
        root, KNOWLEDGE_CONTRACT_MANAGED, label="knowledge contract", old_baseline=old_baseline
    )


def apply_knowledge_contract_install(root: Path, planned: dict[str, str]) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed


def plan_runtime_contract_install(root: Path, old_baseline: str = "") -> dict[str, str]:
    """Install or upgrade only known managed runtime-contract bytes."""
    return plan_managed_file_install(
        root, RUNTIME_CONTRACT_MANAGED, label="runtime contract", old_baseline=old_baseline
    )


def apply_runtime_contract_install(root: Path, planned: dict[str, str]) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed


def plan_skills_contract_install(root: Path, old_baseline: str = "") -> dict[str, str]:
    """Install or upgrade only known managed skills-contract bytes."""
    return plan_managed_file_install(
        root, SKILLS_CONTRACT_MANAGED, label="skills contract", old_baseline=old_baseline
    )


def apply_skills_contract_install(root: Path, planned: dict[str, str]) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed


def plan_implementation_preflight_install(root: Path, old_baseline: str = "") -> dict[str, str]:
    return plan_managed_file_install(
        root, IMPLEMENTATION_PREFLIGHT_MANAGED, label="implementation preflight", old_baseline=old_baseline
    )


def apply_implementation_preflight_install(root: Path, planned: dict[str, str]) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed


def plan_terminal_completion_notify_install(root: Path, old_baseline: str = "") -> dict[str, str]:
    """Install or upgrade only the managed terminal completion notifier."""
    return plan_managed_file_install(
        root,
        TERMINAL_COMPLETION_NOTIFY_MANAGED,
        label="terminal completion notifier",
        old_baseline=old_baseline,
    )


def apply_terminal_completion_notify_install(
    root: Path, planned: dict[str, str]
) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed


def plan_execution_profile_install(root: Path, old_baseline: str = "") -> dict[str, str]:
    """Install or upgrade the managed execution-profile root artifacts."""
    return plan_managed_file_install(
        root, EXECUTION_PROFILE_MANAGED, label="execution profile", old_baseline=old_baseline
    )


def apply_execution_profile_install(root: Path, planned: dict[str, str]) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed


def plan_context_epoch_install(root: Path, old_baseline: str = "") -> dict[str, str]:
    """Install or upgrade only known managed context-epoch bytes."""
    return plan_managed_file_install(
        root, CONTEXT_EPOCH_MANAGED, label="context epoch", old_baseline=old_baseline
    )


def apply_context_epoch_install(root: Path, planned: dict[str, str]) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed


def plan_engineering_context_install(root: Path, old_baseline: str = "") -> dict[str, str]:
    """Install or upgrade only known managed engineering-context bytes."""
    return plan_managed_file_install(
        root, ENGINEERING_CONTEXT_MANAGED, label="engineering context", old_baseline=old_baseline
    )


def apply_engineering_context_install(root: Path, planned: dict[str, str]) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed


def plan_governance_floor_install(root: Path, old_baseline: str = "") -> dict[str, str]:
    """Install or upgrade the managed base-owned governance floor helper."""
    return plan_managed_file_install(
        root, GOVERNANCE_FLOOR_MANAGED, label="governance floor", old_baseline=old_baseline
    )


def apply_governance_floor_install(root: Path, planned: dict[str, str]) -> list[str]:
    installed: list[str] = []
    for rel, text in planned.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append(rel)
    return installed


def git_blob_sha(text: str) -> str:
    data = text.encode("utf-8")
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def build_root_migration_manifest(
    *,
    base_sha: str,
    from_epoch: int,
    to_epoch: int,
    planned_root_surfaces: dict[str, str],
    old_baseline: str,
    new_baseline: str,
) -> dict[str, object] | None:
    if not planned_root_surfaces:
        return None
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise SystemExit("FAIL root migration base HEAD is unavailable")
    if to_epoch != from_epoch + 1:
        raise SystemExit("FAIL root migration policy_epoch must advance exactly once")
    return {
        "contract_version": 1,
        "base_sha": base_sha,
        "from_policy_epoch": from_epoch,
        "to_policy_epoch": to_epoch,
        "requires_exact_head_validate": True,
        "automation_eligible": False,
        "rationale": (
            f"Managed adoption upgrade {old_baseline} -> {new_baseline} changes "
            "canonical governance root surfaces."
        ),
        "changed_surfaces": [
            {"path": rel, "head_blob_sha": git_blob_sha(text)}
            for rel, text in sorted(planned_root_surfaces.items())
        ],
    }


def write_root_migration_manifest(root: Path, manifest: dict[str, object] | None) -> bool:
    if manifest is None:
        return False
    path = root / ROOT_MIGRATION_MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(path, manifest)
    return True


def plan_verification_contract_install(root: Path, old_baseline: str = "") -> dict[str, str]:
    """Install or upgrade only known managed verification-contract bytes."""
    return plan_managed_file_install(
        root, VERIFICATION_CONTRACT_MANAGED, label="verification contract", old_baseline=old_baseline
    )


def apply_verification_contract_install(root: Path, planned: dict[str, str]) -> list[str]:
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
    root: Path, old_version: str, old_baseline: str, new_version: str, new_baseline: str,
    source_overrides: dict[str, str] | None = None,
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
        source = (source_overrides or {}).get(rel, original)
        try:
            rewritten, _changed = rewrite_known_baseline_declarations(
                source, new_version, new_baseline
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
        if rewritten != original:
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
    parser.add_argument("--release-execution-context", default="", choices=("", "github-hosted", "protected-production"))
    parser.add_argument("--operational-e2e-command", default="")
    parser.add_argument("--public-smoke-command", default="")
    parser.add_argument("--full-e2e-passes", type=int, default=-1)
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir() or not run_git(root, "rev-parse", "--show-toplevel"):
        raise SystemExit("FAIL target must be a Git repository")
    if run_git(root, "status", "--porcelain") and not args.allow_dirty:
        raise SystemExit("FAIL target worktree is dirty; preserve unrelated work before upgrade")
    base_head = run_git(root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", base_head):
        raise SystemExit("FAIL target base HEAD is unavailable")

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
    existing_policy_epoch = engineering.get("policy_epoch", 0)
    if (
        isinstance(existing_policy_epoch, bool)
        or not isinstance(existing_policy_epoch, int)
        or existing_policy_epoch < 0
    ):
        raise SystemExit("FAIL existing adoption has invalid policy_epoch")
    target_policy_epoch = max(existing_policy_epoch, POLICY_EPOCH)
    current_version = canonical_version()
    new_baseline = canonical_baseline(args.baseline_sha)
    retired_agent_artifacts = existing_retired_agent_artifacts(root)
    ci_mode = str(engineering.get("ci_mode") or "")
    if ci_mode not in {"shared", "native"}:
        raise SystemExit("FAIL existing adoption has invalid ci_mode")

    release_execution_context = (
        args.release_execution_context
        if args.release_execution_context
        else release.get("execution_context", "github-hosted")
    )
    if (
        not isinstance(release_execution_context, str)
        or release_execution_context not in {"github-hosted", "protected-production"}
    ):
        raise SystemExit("FAIL release execution_context is unsupported")

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
        planned_work_packet_template = plan_work_packet_template_install(root, old_baseline)
        planned_dependencies = plan_engineering_system_dependencies_install(root, old_baseline)
        planned_knowledge_contract = plan_knowledge_contract_install(root, old_baseline)
        planned_runtime_contract = plan_runtime_contract_install(root, old_baseline)
        planned_skills_contract = plan_skills_contract_install(root, old_baseline)
        planned_verification_contract = plan_verification_contract_install(root, old_baseline)
        planned_implementation_preflight = plan_implementation_preflight_install(root, old_baseline)
        planned_terminal_completion_notify = plan_terminal_completion_notify_install(
            root, old_baseline
        )
        planned_execution_profile = plan_execution_profile_install(root, old_baseline)
        planned_context_epoch = plan_context_epoch_install(root, old_baseline)
        planned_engineering_context = plan_engineering_context_install(root, old_baseline)
        planned_governance_floor = plan_governance_floor_install(root, old_baseline)
        planned_execution_policy = plan_execution_policy_sync(root)
        planned_root_surfaces = {
            **planned_dependencies,
            **planned_governance_floor,
            **planned_execution_profile,
            **planned_context_epoch,
        }
        target_policy_epoch = max(existing_policy_epoch, POLICY_EPOCH)
        if planned_root_surfaces:
            target_policy_epoch = max(target_policy_epoch, existing_policy_epoch + 1)
        policy_epoch_repair = target_policy_epoch != existing_policy_epoch
        root_migration = build_root_migration_manifest(
            base_sha=base_head,
            from_epoch=existing_policy_epoch,
            to_epoch=target_policy_epoch,
            planned_root_surfaces=planned_root_surfaces,
            old_baseline=old_baseline,
            new_baseline=new_baseline,
        )
        if (
            not planned_work_packet_template
            and not planned_dependencies
            and not planned_knowledge_contract
            and not planned_runtime_contract
            and not planned_skills_contract
            and not planned_verification_contract
            and not planned_implementation_preflight
            and not planned_terminal_completion_notify
            and not planned_execution_profile
            and not planned_context_epoch
            and not planned_engineering_context
            and not planned_governance_floor
            and planned_execution_policy is None
            and not retired_agent_artifacts
            and not policy_epoch_repair
        ):
            print("ADOPTION_UPGRADE=NO_CHANGE")
            return 0
        if planned_work_packet_template:
            print("WORK_PACKET_TEMPLATE_REPAIR=REQUIRED")
        if planned_knowledge_contract:
            print("KNOWLEDGE_CONTRACT_REPAIR=REQUIRED")
        if planned_runtime_contract:
            print("RUNTIME_CONTRACT_REPAIR=REQUIRED")
        if planned_skills_contract:
            print("SKILLS_CONTRACT_REPAIR=REQUIRED")
        if planned_verification_contract:
            print("VERIFICATION_CONTRACT_REPAIR=REQUIRED")
        if planned_implementation_preflight:
            print("IMPLEMENTATION_PREFLIGHT_REPAIR=REQUIRED")
        if planned_terminal_completion_notify:
            print("TERMINAL_COMPLETION_NOTIFY_REPAIR=REQUIRED")
        if planned_context_epoch:
            print("CONTEXT_EPOCH_REPAIR=REQUIRED")
        if planned_engineering_context:
            print("ENGINEERING_CONTEXT_REPAIR=REQUIRED")
        if planned_dependencies:
            print("ENGINEERING_SYSTEM_DEPENDENCIES_REPAIR=REQUIRED")
        if planned_governance_floor:
            print("GOVERNANCE_FLOOR_REPAIR=REQUIRED")
        if planned_execution_profile:
            print("EXECUTION_PROFILE_REPAIR=REQUIRED")
        if policy_epoch_repair:
            print(f"POLICY_EPOCH_REPAIR={target_policy_epoch}")
        if root_migration is not None:
            print("GOVERNANCE_ROOT_MIGRATION=REQUIRED")
        if planned_execution_policy is not None:
            print("EXECUTION_POLICY_REPAIR=REQUIRED")
        if retired_agent_artifacts:
            print("RETIRED_AGENT_ARTIFACTS_REMOVE=" + ",".join(retired_agent_artifacts))
        if args.audit or not args.apply:
            print("ADOPTION_UPGRADE_AUDIT=PASS")
            if not args.apply:
                return 0
        if policy_epoch_repair:
            engineering["policy_epoch"] = target_policy_epoch
            project["engineering_system"] = engineering
            write_yaml(project_path, project)
        if write_root_migration_manifest(root, root_migration):
            print("GOVERNANCE_ROOT_MIGRATION_WRITTEN=YES")
        removed_retired_agent_artifacts = remove_retired_agent_artifacts(root)
        print("RETIRED_AGENT_ARTIFACTS_REMOVED=" + (",".join(removed_retired_agent_artifacts) if removed_retired_agent_artifacts else "<none>"))
        installed_work_packet_template = apply_work_packet_template_install(
            root, planned_work_packet_template
        )
        print("WORK_PACKET_TEMPLATE_SYNCED=" + (",".join(installed_work_packet_template) if installed_work_packet_template else "<none>"))
        installed_dependencies = apply_engineering_system_dependencies_install(
            root, planned_dependencies
        )
        print("ENGINEERING_SYSTEM_DEPENDENCIES_SYNCED=" + (",".join(installed_dependencies) if installed_dependencies else "<none>"))
        installed_knowledge = apply_knowledge_contract_install(root, planned_knowledge_contract)
        print("KNOWLEDGE_CONTRACT_INSTALLED=" + (",".join(installed_knowledge) if installed_knowledge else "<none>"))
        installed_runtime = apply_runtime_contract_install(root, planned_runtime_contract)
        print("RUNTIME_CONTRACT_INSTALLED=" + (",".join(installed_runtime) if installed_runtime else "<none>"))
        installed_skills = apply_skills_contract_install(root, planned_skills_contract)
        print("SKILLS_CONTRACT_INSTALLED=" + (",".join(installed_skills) if installed_skills else "<none>"))
        installed_verification = apply_verification_contract_install(
            root, planned_verification_contract
        )
        print("VERIFICATION_CONTRACT_INSTALLED=" + (",".join(installed_verification) if installed_verification else "<none>"))
        installed_preflight = apply_implementation_preflight_install(
            root, planned_implementation_preflight
        )
        print("IMPLEMENTATION_PREFLIGHT_INSTALLED=" + (",".join(installed_preflight) if installed_preflight else "<none>"))
        installed_terminal_completion_notify = apply_terminal_completion_notify_install(
            root, planned_terminal_completion_notify
        )
        print("TERMINAL_COMPLETION_NOTIFY_INSTALLED=" + (",".join(installed_terminal_completion_notify) if installed_terminal_completion_notify else "<none>"))
        installed_execution_profile = apply_execution_profile_install(
            root, planned_execution_profile
        )
        print("EXECUTION_PROFILE_INSTALLED=" + (",".join(installed_execution_profile) if installed_execution_profile else "<none>"))
        installed_context_epoch = apply_context_epoch_install(root, planned_context_epoch)
        print("CONTEXT_EPOCH_INSTALLED=" + (",".join(installed_context_epoch) if installed_context_epoch else "<none>"))
        installed_engineering_context = apply_engineering_context_install(
            root, planned_engineering_context
        )
        print("ENGINEERING_CONTEXT_INSTALLED=" + (",".join(installed_engineering_context) if installed_engineering_context else "<none>"))
        installed_governance_floor = apply_governance_floor_install(
            root, planned_governance_floor
        )
        print("GOVERNANCE_FLOOR_INSTALLED=" + (",".join(installed_governance_floor) if installed_governance_floor else "<none>"))
        execution_policy_synced = apply_execution_policy_sync(root, planned_execution_policy)
        print("EXECUTION_POLICY_SYNCED=" + ("YES" if execution_policy_synced else "NO"))
        checker = CANONICAL / "tools" / "check-adoption.py"
        result = subprocess.run([sys.executable, str(checker), "--root", str(root)])
        if result.returncode:
            raise SystemExit(result.returncode)
        print("ADOPTION_UPGRADE=PASS")
        return 0

    old_workflow = workflow_path.read_text(encoding="utf-8")
    safe_old_workflows = {
        legacy_engineering_workflow(old_baseline, ci_mode),
        pre_governance_floor_engineering_workflow(old_baseline, ci_mode),
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
    if retired_agent_artifacts:
        print("RETIRED_AGENT_ARTIFACTS_REMOVE=" + ",".join(retired_agent_artifacts))

    if args.audit or not args.apply:
        print("ADOPTION_UPGRADE_AUDIT=PASS")
        if not args.apply:
            return 0

    engineering["version"] = current_version
    engineering["baseline"] = new_baseline
    engineering["policy_epoch"] = target_policy_epoch
    project["engineering_system"] = engineering
    operations["persistent_state"] = persistent_state
    operations["runbook_paths"] = runbook_paths
    operations["health_command"] = health_command
    operations["backup_command"] = backup_command
    operations["restore_test_command"] = restore_test_command
    operations["upgrade_command"] = upgrade_command
    operations["rollback_command"] = rollback_command
    project["operations"] = operations

    release["execution_context"] = release_execution_context
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
    # Preserve the established declaration-validation order before composing
    # the managed execution-policy repair into the same AGENTS.md write.
    plan_baseline_declaration_updates(
        root, old_version, old_baseline, current_version, new_baseline
    )
    planned_execution_policy = plan_execution_policy_sync(root)
    source_overrides = (
        {"AGENTS.md": planned_execution_policy}
        if planned_execution_policy is not None
        else None
    )
    planned_declarations = plan_baseline_declaration_updates(
        root, old_version, old_baseline, current_version, new_baseline,
        source_overrides=source_overrides,
    )
    planned_work_packet_template = plan_work_packet_template_install(root, old_baseline)
    planned_dependencies = plan_engineering_system_dependencies_install(root, old_baseline)
    planned_knowledge_contract = plan_knowledge_contract_install(root, old_baseline)
    planned_runtime_contract = plan_runtime_contract_install(root, old_baseline)
    planned_skills_contract = plan_skills_contract_install(root, old_baseline)
    planned_verification_contract = plan_verification_contract_install(root, old_baseline)
    planned_implementation_preflight = plan_implementation_preflight_install(root, old_baseline)
    planned_terminal_completion_notify = plan_terminal_completion_notify_install(
        root, old_baseline
    )
    planned_execution_profile = plan_execution_profile_install(root, old_baseline)
    planned_context_epoch = plan_context_epoch_install(root, old_baseline)
    planned_engineering_context = plan_engineering_context_install(root, old_baseline)
    planned_governance_floor = plan_governance_floor_install(root, old_baseline)
    planned_root_surfaces = {
        **planned_dependencies,
        **planned_governance_floor,
        **planned_execution_profile,
        **planned_context_epoch,
    }
    if planned_root_surfaces:
        target_policy_epoch = max(POLICY_EPOCH, existing_policy_epoch + 1)
        engineering["policy_epoch"] = target_policy_epoch
        project["engineering_system"] = engineering
    root_migration = build_root_migration_manifest(
        base_sha=base_head,
        from_epoch=existing_policy_epoch,
        to_epoch=target_policy_epoch,
        planned_root_surfaces=planned_root_surfaces,
        old_baseline=old_baseline,
        new_baseline=new_baseline,
    )

    write_yaml(project_path, project)
    write_yaml(release_path, release)
    if write_root_migration_manifest(root, root_migration):
        print("GOVERNANCE_ROOT_MIGRATION_WRITTEN=YES")
    workflow_path.write_text(engineering_workflow(new_baseline, ci_mode), encoding="utf-8")

    if release_contract_enabled:
        release_workflow_path.write_text(release_workflow(new_baseline), encoding="utf-8")

    removed_retired_agent_artifacts = remove_retired_agent_artifacts(root)
    print("RETIRED_AGENT_ARTIFACTS_REMOVED=" + (",".join(removed_retired_agent_artifacts) if removed_retired_agent_artifacts else "<none>"))

    installed_work_packet_template = apply_work_packet_template_install(
        root, planned_work_packet_template
    )
    if installed_work_packet_template:
        print("WORK_PACKET_TEMPLATE_SYNCED=" + ",".join(installed_work_packet_template))
    else:
        print("WORK_PACKET_TEMPLATE_SYNCED=<none>")

    installed_dependencies = apply_engineering_system_dependencies_install(
        root, planned_dependencies
    )
    if installed_dependencies:
        print("ENGINEERING_SYSTEM_DEPENDENCIES_SYNCED=" + ",".join(installed_dependencies))
    else:
        print("ENGINEERING_SYSTEM_DEPENDENCIES_SYNCED=<none>")

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

    installed_skills = apply_skills_contract_install(root, planned_skills_contract)
    if installed_skills:
        print("SKILLS_CONTRACT_INSTALLED=" + ",".join(installed_skills))
    else:
        print("SKILLS_CONTRACT_INSTALLED=<none>")

    installed_verification = apply_verification_contract_install(root, planned_verification_contract)
    if installed_verification:
        print("VERIFICATION_CONTRACT_INSTALLED=" + ",".join(installed_verification))
    else:
        print("VERIFICATION_CONTRACT_INSTALLED=<none>")

    installed_preflight = apply_implementation_preflight_install(
        root, planned_implementation_preflight
    )
    if installed_preflight:
        print("IMPLEMENTATION_PREFLIGHT_INSTALLED=" + ",".join(installed_preflight))
    else:
        print("IMPLEMENTATION_PREFLIGHT_INSTALLED=<none>")

    installed_terminal_completion_notify = apply_terminal_completion_notify_install(
        root, planned_terminal_completion_notify
    )
    if installed_terminal_completion_notify:
        print(
            "TERMINAL_COMPLETION_NOTIFY_INSTALLED="
            + ",".join(installed_terminal_completion_notify)
        )
    else:
        print("TERMINAL_COMPLETION_NOTIFY_INSTALLED=<none>")

    installed_execution_profile = apply_execution_profile_install(
        root, planned_execution_profile
    )
    if installed_execution_profile:
        print("EXECUTION_PROFILE_INSTALLED=" + ",".join(installed_execution_profile))
    else:
        print("EXECUTION_PROFILE_INSTALLED=<none>")

    installed_context_epoch = apply_context_epoch_install(root, planned_context_epoch)
    if installed_context_epoch:
        print("CONTEXT_EPOCH_INSTALLED=" + ",".join(installed_context_epoch))
    else:
        print("CONTEXT_EPOCH_INSTALLED=<none>")

    installed_engineering_context = apply_engineering_context_install(
        root, planned_engineering_context
    )
    if installed_engineering_context:
        print("ENGINEERING_CONTEXT_INSTALLED=" + ",".join(installed_engineering_context))
    else:
        print("ENGINEERING_CONTEXT_INSTALLED=<none>")

    installed_governance_floor = apply_governance_floor_install(
        root, planned_governance_floor
    )
    if installed_governance_floor:
        print("GOVERNANCE_FLOOR_INSTALLED=" + ",".join(installed_governance_floor))
    else:
        print("GOVERNANCE_FLOOR_INSTALLED=<none>")

    synced_declarations = apply_baseline_declaration_updates(root, planned_declarations)
    if synced_declarations:
        print("BASELINE_DECLARATIONS_SYNCED=" + ",".join(synced_declarations))
    else:
        print("BASELINE_DECLARATIONS_SYNCED=<none>")

    execution_policy_synced = planned_execution_policy is not None
    print("EXECUTION_POLICY_SYNCED=" + ("YES" if execution_policy_synced else "NO"))

    checker = CANONICAL / "tools" / "check-adoption.py"
    result = subprocess.run([sys.executable, str(checker), "--root", str(root)])
    if result.returncode:
        raise SystemExit(result.returncode)

    print("ADOPTION_UPGRADE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
