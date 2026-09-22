#!/usr/bin/env python3
"""Organization-wide Engineering System rollout audit/apply helper.

Default mode is audit/dry-run. Apply mode creates isolated per-repository
branches (and optional PRs) and reuses tools/adopt.py and
tools/upgrade-adoption.py rather than reimplementing adoption contracts.

Optional versioned override manifests supply repository-specific adoption or
upgrade inputs that cannot be safely inferred, enabling one-command apply for a
reviewed organization while preserving per-repository fail-closed behavior.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from adopt import canonical_baseline, canonical_version

CANONICAL = Path(__file__).resolve().parents[1]
ADOPT = CANONICAL / "tools" / "adopt.py"
UPGRADE = CANONICAL / "tools" / "upgrade-adoption.py"
CHECK = CANONICAL / "tools" / "check-adoption.py"

STATES = (
    "CURRENT",
    "OUTDATED",
    "UNADOPTED",
    "ARCHIVED",
    "INCOMPLETE",
    "ERROR",
    "EXCLUDED",
)

OVERRIDE_MANIFEST_VERSION = 1

# Scalar CLI flags shared by adopt/upgrade helpers.
ADOPT_SCALAR_FLAGS = {
    "test_command": "--test-command",
    "setup_command": "--setup-command",
    "build_command": "--build-command",
    "lint_command": "--lint-command",
    "typecheck_command": "--typecheck-command",
    "release_command": "--release-command",
    "release_setup_command": "--release-setup-command",
    "preflight_command": "--preflight-command",
    "artifact_hash_command": "--artifact-hash-command",
    "provenance_command": "--provenance-command",
    "sbom_command": "--sbom-command",
    "operational_e2e_command": "--operational-e2e-command",
    "public_smoke_command": "--public-smoke-command",
    "baseline_sha": "--baseline-sha",
    "project_type": "--project-type",
    "ci_mode": "--ci-mode",
    "merge_gate_status": "--merge-gate-status",
    "maturity": "--maturity",
    "operations_mode": "--operations-mode",
    "health_command": "--health-command",
    "backup_command": "--backup-command",
    "restore_test_command": "--restore-test-command",
    "upgrade_command": "--upgrade-command",
    "rollback_command": "--rollback-command",
    "domain": "--domain",
    "platform": "--platform",
}

UPGRADE_SCALAR_FLAGS = {
    "persistent_state": "--persistent-state",
    "health_command": "--health-command",
    "backup_command": "--backup-command",
    "restore_test_command": "--restore-test-command",
    "upgrade_command": "--upgrade-command",
    "rollback_command": "--rollback-command",
    "release_setup_command": "--release-setup-command",
    "preflight_command": "--preflight-command",
    "release_command": "--release-command",
    "artifact_hash_command": "--artifact-hash-command",
    "provenance_command": "--provenance-command",
    "sbom_command": "--sbom-command",
    "operational_e2e_command": "--operational-e2e-command",
    "public_smoke_command": "--public-smoke-command",
}


class CheckoutError(RuntimeError):
    """Per-repository checkout/clone failure that must not abort the org run."""


@dataclass
class RepoRecord:
    full_name: str
    archived: bool = False
    default_branch: str = "main"
    local_path: str = ""
    clone_url: str = ""


@dataclass
class RepoResult:
    full_name: str
    state: str
    action: str
    version: str = ""
    baseline: str = ""
    mode: str = ""
    detail: str = ""
    branch: str = ""
    pr_url: str = ""
    outcome: str = "SKIPPED"


@dataclass
class OverrideManifest:
    version: int = OVERRIDE_MANIFEST_VERSION
    defaults: dict[str, Any] = field(default_factory=dict)
    repositories: dict[str, dict[str, Any]] = field(default_factory=dict)

    def for_repo(self, full_name: str) -> dict[str, Any]:
        merged = dict(self.defaults)
        merged.update(self.repositories.get(full_name) or {})
        return merged


def run_git(root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
    )


def run_cmd(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    return data


def semver_tuple(value: str) -> tuple[int, int, int] | None:
    core = value.split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def flatten_paginated_payload(payload: Any) -> list[Any]:
    """Flatten gh --paginate --slurp output into a single list of items."""
    if isinstance(payload, list):
        if not payload:
            return []
        if all(isinstance(item, list) for item in payload):
            flattened: list[Any] = []
            for page in payload:
                flattened.extend(page)
            return flattened
        return payload
    raise SystemExit("FAIL unexpected org repository inventory payload")


def gh_json(args: list[str]) -> Any:
    completed = run_cmd(["gh", *args])
    if completed.returncode != 0:
        raise SystemExit(f"FAIL authenticated gh inventory failed: {completed.stdout.strip()}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"FAIL gh returned invalid JSON: {exc}") from exc


def inventory_from_org(org: str) -> list[RepoRecord]:
    payload = gh_json(
        [
            "api",
            "--paginate",
            "--slurp",
            f"/orgs/{org}/repos?per_page=100&type=all",
        ]
    )
    items = flatten_paginated_payload(payload)
    records: list[RepoRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        full_name = str(item.get("full_name") or "").strip()
        if not full_name:
            continue
        records.append(
            RepoRecord(
                full_name=full_name,
                archived=bool(item.get("archived")),
                default_branch=str(item.get("default_branch") or "main"),
                clone_url=str(item.get("clone_url") or ""),
            )
        )
    return sorted(records, key=lambda row: row.full_name)


def inventory_from_file(path: Path) -> list[RepoRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit("FAIL inventory file must contain a JSON array")
    records: list[RepoRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            raise SystemExit("FAIL inventory file entries must be objects")
        full_name = str(item.get("full_name") or "").strip()
        if not full_name:
            raise SystemExit("FAIL inventory entry missing full_name")
        records.append(
            RepoRecord(
                full_name=full_name,
                archived=bool(item.get("archived")),
                default_branch=str(item.get("default_branch") or "main"),
                local_path=str(item.get("local_path") or "").strip(),
                clone_url=str(item.get("clone_url") or "").strip(),
            )
        )
    return sorted(records, key=lambda row: row.full_name)


def load_override_manifest(path: Path) -> OverrideManifest:
    data = load_yaml(path)
    if not data:
        raise SystemExit(f"FAIL override manifest is empty or invalid: {path}")
    version = data.get("version")
    if version != OVERRIDE_MANIFEST_VERSION:
        raise SystemExit(
            f"FAIL unsupported override manifest version {version!r}; expected {OVERRIDE_MANIFEST_VERSION}"
        )
    defaults = data.get("defaults") or {}
    repositories = data.get("repositories") or {}
    if not isinstance(defaults, dict):
        raise SystemExit("FAIL override manifest defaults must be a mapping")
    if not isinstance(repositories, dict):
        raise SystemExit("FAIL override manifest repositories must be a mapping")
    normalized: dict[str, dict[str, Any]] = {}
    for name, entry in repositories.items():
        full_name = str(name).strip()
        if not full_name:
            raise SystemExit("FAIL override manifest repository key must be owner/name")
        if entry is None:
            normalized[full_name] = {}
            continue
        if not isinstance(entry, dict):
            raise SystemExit(f"FAIL override for {full_name} must be a mapping")
        normalized[full_name] = entry
    return OverrideManifest(version=int(version), defaults=defaults, repositories=normalized)


def structural_adoption_ok(root: Path) -> tuple[bool, str]:
    completed = run_cmd([sys.executable, str(CHECK), "--root", str(root)])
    if completed.returncode == 0 and "ENGINEERING_SYSTEM_ADOPTION=PASS" in completed.stdout:
        return True, "structural adoption validation passed"
    detail = completed.stdout.strip().splitlines()
    summary = detail[-1] if detail else "structural adoption validation failed"
    return False, summary


def classify_checkout(root: Path, target_version: str, target_baseline: str) -> RepoResult:
    project_path = root / ".engineering" / "project.yaml"
    adoption_markers = any(
        (root / rel).exists()
        for rel in (
            ".engineering",
            ".cursor/rules/engineering-system.mdc",
            ".github/workflows/engineering-system.yml",
            ".cursor/commands/resume.md",
        )
    )
    agents = (root / "AGENTS.md").is_file()
    if not project_path.is_file():
        if adoption_markers or agents:
            return RepoResult(
                full_name="",
                state="INCOMPLETE",
                action="NEEDS_INPUT",
                detail="adoption markers present without .engineering/project.yaml",
            )
        return RepoResult(
            full_name="",
            state="UNADOPTED",
            action="ADOPT",
            detail="no managed Engineering System metadata",
        )

    try:
        project = load_yaml(project_path)
    except Exception as exc:  # noqa: BLE001 - surface parse failures precisely
        return RepoResult(
            full_name="",
            state="ERROR",
            action="NEEDS_INPUT",
            detail=f"cannot parse project.yaml: {exc}",
        )

    engineering = project.get("engineering_system") or {}
    version = str(engineering.get("version") or "").strip()
    baseline = str(engineering.get("baseline") or "").strip()
    mode = str(engineering.get("mode") or "").strip()
    if not agents:
        return RepoResult(
            full_name="",
            state="INCOMPLETE",
            action="NEEDS_INPUT",
            version=version,
            baseline=baseline,
            mode=mode,
            detail="missing mandatory AGENTS.md for adopted-project context",
        )
    if mode not in {"adopted", "canonical"}:
        return RepoResult(
            full_name="",
            state="INCOMPLETE",
            action="NEEDS_INPUT",
            version=version,
            baseline=baseline,
            mode=mode,
            detail="engineering_system.mode missing or unsupported",
        )

    current_semver = semver_tuple(version)
    target_semver = semver_tuple(target_version)
    if current_semver is None or target_semver is None:
        return RepoResult(
            full_name="",
            state="ERROR",
            action="NEEDS_INPUT",
            version=version,
            baseline=baseline,
            mode=mode,
            detail="invalid Engineering System version",
        )

    if mode == "canonical":
        if version == target_version and (not target_baseline or baseline == target_baseline or not baseline):
            return RepoResult(
                full_name="",
                state="CURRENT",
                action="NONE",
                version=version,
                baseline=baseline,
                mode=mode,
                detail="canonical repository already at target version",
            )
        return RepoResult(
            full_name="",
            state="OUTDATED",
            action="NEEDS_INPUT",
            version=version,
            baseline=baseline,
            mode=mode,
            detail="canonical repository version drift requires manual release process",
        )

    if version == target_version and target_baseline and baseline == target_baseline:
        ok, detail = structural_adoption_ok(root)
        if not ok:
            return RepoResult(
                full_name="",
                state="INCOMPLETE",
                action="NEEDS_INPUT",
                version=version,
                baseline=baseline,
                mode=mode,
                detail=f"version/baseline match but adoption incomplete: {detail}",
            )
        return RepoResult(
            full_name="",
            state="CURRENT",
            action="NONE",
            version=version,
            baseline=baseline,
            mode=mode,
            detail="managed adoption already current",
        )

    if version == target_version and target_baseline and baseline != target_baseline:
        return RepoResult(
            full_name="",
            state="OUTDATED",
            action="UPGRADE",
            version=version,
            baseline=baseline,
            mode=mode,
            detail=f"same version {version} pinned to different baseline; upgrade toward {target_baseline}",
        )

    if current_semver > target_semver:
        return RepoResult(
            full_name="",
            state="ERROR",
            action="NEEDS_INPUT",
            version=version,
            baseline=baseline,
            mode=mode,
            detail=f"repository version {version} is newer than canonical {target_version}",
        )

    return RepoResult(
        full_name="",
        state="OUTDATED",
        action="UPGRADE",
        version=version,
        baseline=baseline,
        mode=mode,
        detail=f"managed upgrade from {version} toward {target_version}",
    )


def ensure_checkout(record: RepoRecord, workdir: Path) -> Path:
    if record.local_path:
        path = Path(record.local_path).expanduser().resolve()
        if not path.is_dir():
            raise CheckoutError(f"local_path missing for {record.full_name}: {path}")
        return path

    if not record.clone_url:
        raise CheckoutError(f"no clone_url or local_path for {record.full_name}")

    dest = workdir / record.full_name.replace("/", "__")
    if dest.exists():
        shutil.rmtree(dest)
    completed = run_cmd(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            record.default_branch,
            record.clone_url,
            str(dest),
        ]
    )
    if completed.returncode != 0:
        raise CheckoutError(f"clone {record.full_name}: {completed.stdout.strip()}")
    return dest


def create_rollout_branch(root: Path, default_branch: str, branch_name: str) -> None:
    status = run_git(root, "status", "--porcelain")
    if status.stdout.strip():
        raise SystemExit(f"FAIL refusing dirty worktree before rollout branch: {root}")
    current = run_git(root, "branch", "--show-current").stdout.strip()
    if default_branch and current != default_branch:
        switched = run_git(root, "checkout", default_branch)
        if switched.returncode != 0:
            # Local fixtures may use a different initial branch name; stay on current.
            pass
    created = run_git(root, "checkout", "-B", branch_name)
    if created.returncode != 0:
        raise SystemExit(f"FAIL cannot create rollout branch {branch_name}: {created.stdout.strip()}")


def commit_rollout_changes(root: Path, message: str) -> bool:
    run_git(root, "add", "-A")
    status = run_git(root, "status", "--porcelain")
    if not status.stdout.strip():
        return False
    committed = run_git(root, "commit", "-qm", message)
    if committed.returncode != 0:
        raise SystemExit(f"FAIL commit rollout changes: {committed.stdout.strip()}")
    return True


def maybe_create_pr(root: Path, branch_name: str, title: str, body: str, create_pr: bool) -> str:
    if not create_pr:
        return ""
    push = run_git(root, "push", "-u", "origin", branch_name)
    if push.returncode != 0:
        raise SystemExit(f"FAIL push rollout branch: {push.stdout.strip()}")
    pr = run_cmd(
        [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--head",
            branch_name,
        ],
        cwd=root,
    )
    if pr.returncode != 0:
        raise SystemExit(f"FAIL create rollout PR: {pr.stdout.strip()}")
    return pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else ""


def append_bool_flag(argv: list[str], enabled: bool, flag: str) -> None:
    if enabled:
        argv.append(flag)


def append_scalar(argv: list[str], override: dict[str, Any], key: str, flag: str) -> None:
    if key not in override or override[key] is None:
        return
    value = override[key]
    if isinstance(value, bool):
        return
    text = str(value).strip()
    if text:
        argv.extend([flag, text])


def append_repeatable(argv: list[str], override: dict[str, Any], key: str, flag: str) -> None:
    if key not in override or override[key] is None:
        return
    value = override[key]
    items = value if isinstance(value, list) else [value]
    for item in items:
        text = str(item).strip()
        if text:
            argv.extend([flag, text])


def build_adopt_argv(root: Path, target_baseline: str, override: dict[str, Any]) -> list[str]:
    argv = [
        sys.executable,
        str(ADOPT),
        "--root",
        str(root),
        "--apply",
        "--baseline-sha",
        str(override.get("baseline_sha") or target_baseline),
    ]
    append_bool_flag(argv, bool(override.get("ack_rule_review")), "--ack-rule-review")
    append_bool_flag(argv, bool(override.get("allow_no_tests")), "--allow-no-tests")
    append_bool_flag(argv, bool(override.get("allow_dirty")), "--allow-dirty")
    append_bool_flag(argv, bool(override.get("persistent_state")), "--persistent-state")
    for key, flag in ADOPT_SCALAR_FLAGS.items():
        if key == "baseline_sha":
            continue
        append_scalar(argv, override, key, flag)
    append_repeatable(argv, override, "native_ci_workflows", "--native-ci-workflow")
    append_repeatable(argv, override, "runbook_paths", "--runbook-path")
    append_repeatable(argv, override, "domain_tests", "--domain-test")
    if "full_e2e_passes" in override and override["full_e2e_passes"] is not None:
        argv.extend(["--full-e2e-passes", str(int(override["full_e2e_passes"]))])
    return argv


def build_upgrade_argv(root: Path, target_baseline: str, override: dict[str, Any]) -> list[str]:
    argv = [
        sys.executable,
        str(UPGRADE),
        "--root",
        str(root),
        "--apply",
        "--baseline-sha",
        str(override.get("baseline_sha") or target_baseline),
    ]
    append_bool_flag(argv, bool(override.get("allow_dirty")), "--allow-dirty")
    if "persistent_state" in override and override["persistent_state"] is not None:
        value = override["persistent_state"]
        if isinstance(value, bool):
            argv.extend(["--persistent-state", "yes" if value else "no"])
        else:
            text = str(value).strip()
            if text:
                argv.extend(["--persistent-state", text])
    for key, flag in UPGRADE_SCALAR_FLAGS.items():
        if key == "persistent_state":
            continue
        append_scalar(argv, override, key, flag)
    append_repeatable(argv, override, "runbook_paths", "--runbook-path")
    if "full_e2e_passes" in override and override["full_e2e_passes"] is not None:
        argv.extend(["--full-e2e-passes", str(int(override["full_e2e_passes"]))])
    return argv


def apply_upgrade(root: Path, target_baseline: str, override: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return run_cmd(build_upgrade_argv(root, target_baseline, override))


def process_repo(
    record: RepoRecord,
    *,
    apply: bool,
    include_archived: bool,
    target_version: str,
    target_baseline: str,
    branch_prefix: str,
    workdir: Path,
    create_pr: bool,
    override: dict[str, Any],
) -> RepoResult:
    if bool(override.get("exclude")):
        return RepoResult(
            full_name=record.full_name,
            state="EXCLUDED",
            action="SKIP_EXCLUDED",
            detail="repository excluded by override manifest",
            outcome="SKIPPED",
        )

    if record.archived and not include_archived:
        return RepoResult(
            full_name=record.full_name,
            state="ARCHIVED",
            action="SKIP_ARCHIVED",
            detail="archived repositories are excluded unless --include-archived is set",
            outcome="SKIPPED",
        )

    try:
        root = ensure_checkout(record, workdir)
    except CheckoutError as exc:
        return RepoResult(
            full_name=record.full_name,
            state="ERROR",
            action="NEEDS_INPUT",
            detail=str(exc),
            outcome="FAIL",
        )

    result = classify_checkout(root, target_version, target_baseline)
    result.full_name = record.full_name

    if record.archived:
        result.state = "ARCHIVED"
        if result.action == "NONE":
            result.action = "SKIP_ARCHIVED"
        result.outcome = "SKIPPED"
        result.detail = (result.detail + "; archived repository included for classification only").strip("; ")
        return result

    if result.action in {"NONE", "SKIP_ARCHIVED", "SKIP_EXCLUDED"}:
        result.outcome = "NO_CHANGE"
        return result

    if result.action == "NEEDS_INPUT" or result.state in {"INCOMPLETE", "ERROR"}:
        result.outcome = "NEEDS_INPUT"
        return result

    if not apply:
        result.outcome = "PLANNED"
        return result

    branch_name = f"{branch_prefix}{target_version.replace('.', '-')}"
    try:
        create_rollout_branch(root, record.default_branch, branch_name)
    except SystemExit as exc:
        return RepoResult(
            full_name=record.full_name,
            state="ERROR",
            action=result.action,
            version=result.version,
            baseline=result.baseline,
            mode=result.mode,
            detail=str(exc),
            outcome="FAIL",
        )
    result.branch = branch_name

    if result.action == "UPGRADE":
        upgraded = apply_upgrade(root, target_baseline, override)
        if upgraded.returncode != 0:
            result.state = "ERROR"
            result.outcome = "FAIL"
            result.detail = upgraded.stdout.strip() or "upgrade-adoption.py failed"
            return result
        if "ADOPTION_UPGRADE=NO_CHANGE" in upgraded.stdout:
            result.state = "CURRENT"
            result.action = "NONE"
            result.outcome = "NO_CHANGE"
            result.detail = "upgrade helper reported NO_CHANGE"
            return result
        commit_rollout_changes(
            root,
            f"chore: upgrade Engineering System adoption to {target_version}",
        )
        result.pr_url = maybe_create_pr(
            root,
            branch_name,
            f"Upgrade Engineering System to {target_version}",
            (
                f"Automated org-wide rollout upgrade for `{record.full_name}`.\n\n"
                f"Target version: `{target_version}`\n"
                f"Baseline: `{target_baseline}`\n"
            ),
            create_pr,
        )
        result.outcome = "APPLIED"
        result.version = target_version
        result.baseline = target_baseline
        result.detail = "managed upgrade applied on isolated rollout branch"
        return result

    if result.action == "ADOPT":
        attempted = run_cmd(build_adopt_argv(root, target_baseline, override))
        if attempted.returncode != 0:
            result.state = "UNADOPTED"
            result.action = "NEEDS_INPUT"
            result.outcome = "NEEDS_INPUT"
            result.detail = attempted.stdout.strip() or "adopt.py require explicit inputs"
            return result
        commit_rollout_changes(
            root,
            f"chore: adopt Engineering System {target_version}",
        )
        result.pr_url = maybe_create_pr(
            root,
            branch_name,
            f"Adopt Engineering System {target_version}",
            (
                f"Automated org-wide rollout adoption for `{record.full_name}`.\n\n"
                f"Target version: `{target_version}`\n"
                f"Baseline: `{target_baseline}`\n"
            ),
            create_pr,
        )
        result.state = "CURRENT"
        result.action = "ADOPT"
        result.outcome = "APPLIED"
        result.version = target_version
        result.baseline = target_baseline
        result.detail = "adoption bootstrap applied on isolated rollout branch"
        return result

    result.outcome = "NEEDS_INPUT"
    return result


def print_result(result: RepoResult) -> None:
    print(f"REPO={result.full_name}")
    print(f"STATE={result.state}")
    print(f"ACTION={result.action}")
    print(f"OUTCOME={result.outcome}")
    print(f"VERSION={result.version or '<none>'}")
    print(f"BASELINE={result.baseline or '<none>'}")
    print(f"MODE={result.mode or '<none>'}")
    print(f"BRANCH={result.branch or '<none>'}")
    print(f"PR_URL={result.pr_url or '<none>'}")
    detail = result.detail.replace("\n", "\\n") if result.detail else "<none>"
    print(f"DETAIL={detail}")
    print("---")


def summarize(results: list[RepoResult], apply: bool) -> int:
    counts = {state: 0 for state in STATES}
    outcomes = {"APPLIED": 0, "PLANNED": 0, "NO_CHANGE": 0, "SKIPPED": 0, "NEEDS_INPUT": 0, "FAIL": 0}
    for result in results:
        counts[result.state] = counts.get(result.state, 0) + 1
        outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1

    print("ORG_ROLLOUT_SUMMARY")
    for state, count in counts.items():
        print(f"COUNT_{state}={count}")
    for outcome, count in outcomes.items():
        print(f"OUTCOME_{outcome}={count}")

    hard_fail = outcomes.get("FAIL", 0)
    needs_input = outcomes.get("NEEDS_INPUT", 0)
    if hard_fail:
        print("ORG_ROLLOUT=FAIL")
        print("HUMAN_SUMMARY=One or more repositories failed during rollout; do not treat partial work as PASS.")
        return 1
    if needs_input:
        print("ORG_ROLLOUT=PARTIAL")
        print(
            "HUMAN_SUMMARY=Rollout completed with repositories that need explicit adoption/upgrade inputs; "
            "inspect NEEDS_INPUT rows before broader apply."
        )
        return 2 if apply else 0
    print("ORG_ROLLOUT=PASS")
    if apply:
        print("HUMAN_SUMMARY=All actionable repositories were processed without hard failures.")
    else:
        print("HUMAN_SUMMARY=Audit complete; no repository mutations were performed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit or apply organization-wide Engineering System rollout")
    parser.add_argument("--org", default="", help="GitHub organization login to inventory")
    parser.add_argument("--inventory-file", default="", help="JSON inventory fixture/override")
    parser.add_argument(
        "--override-manifest",
        default="",
        help="Optional versioned YAML/JSON rollout override manifest for per-repository inputs",
    )
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--audit", action="store_true", help="Explicit audit/dry-run (default)")
    parser.add_argument("--apply", action="store_true", help="Create isolated rollout branches and optional PRs")
    parser.add_argument("--create-pr", action="store_true", help="Push branch and open PR during --apply")
    parser.add_argument("--workdir", default="", help="Working directory for clones")
    parser.add_argument("--baseline-sha", default="", help="Immutable canonical baseline SHA for audit/apply")
    parser.add_argument("--branch-prefix", default="chore/engineering-system-rollout-")
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help="Optional owner/name filter; may be repeated",
    )
    args = parser.parse_args()

    if args.apply and args.audit:
        raise SystemExit("FAIL choose either --audit or --apply")
    apply = bool(args.apply)
    if args.create_pr and not apply:
        raise SystemExit("FAIL --create-pr requires --apply")

    target_version = canonical_version()
    # Always resolve an immutable baseline so audit compares pins, not version alone.
    target_baseline = canonical_baseline(args.baseline_sha)
    if apply and not target_baseline:
        raise SystemExit("FAIL --apply requires --baseline-sha or a resolvable canonical HEAD")

    overrides = OverrideManifest()
    if args.override_manifest:
        overrides = load_override_manifest(Path(args.override_manifest))

    if args.inventory_file:
        records = inventory_from_file(Path(args.inventory_file))
    elif args.org:
        records = inventory_from_org(args.org)
    else:
        raise SystemExit("FAIL provide --org or --inventory-file")

    if args.repo:
        wanted = {item.strip() for item in args.repo if item.strip()}
        records = [row for row in records if row.full_name in wanted]
        missing = sorted(wanted - {row.full_name for row in records})
        if missing:
            raise SystemExit("FAIL requested repos missing from inventory: " + ",".join(missing))

    print(f"ORG_ROLLOUT_MODE={'APPLY' if apply else 'AUDIT'}")
    print(f"TARGET_VERSION={target_version}")
    print(f"TARGET_BASELINE={target_baseline}")
    print(f"REPO_COUNT={len(records)}")
    print(f"INCLUDE_ARCHIVED={'YES' if args.include_archived else 'NO'}")
    print(f"OVERRIDE_MANIFEST={'YES' if args.override_manifest else 'NO'}")

    workdir = Path(args.workdir).expanduser().resolve() if args.workdir else Path(tempfile.mkdtemp(prefix="org-rollout-"))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"WORKDIR={workdir}")

    results: list[RepoResult] = []
    for record in records:
        result = process_repo(
            record,
            apply=apply,
            include_archived=bool(args.include_archived),
            target_version=target_version,
            target_baseline=target_baseline,
            branch_prefix=args.branch_prefix,
            workdir=workdir,
            create_pr=bool(args.create_pr),
            override=overrides.for_repo(record.full_name),
        )
        print_result(result)
        results.append(result)

    return summarize(results, apply)


if __name__ == "__main__":
    raise SystemExit(main())
