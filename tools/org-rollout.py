#!/usr/bin/env python3
"""Organization-wide Engineering System rollout audit/apply helper.

Default mode is audit/dry-run. Apply mode creates isolated per-repository
branches (and optional PRs) and reuses tools/adopt.py and
tools/upgrade-adoption.py rather than reimplementing adoption contracts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from adopt import canonical_baseline, canonical_version

CANONICAL = Path(__file__).resolve().parents[1]
ADOPT = CANONICAL / "tools" / "adopt.py"
UPGRADE = CANONICAL / "tools" / "upgrade-adoption.py"

STATES = (
    "CURRENT",
    "OUTDATED",
    "UNADOPTED",
    "ARCHIVED",
    "INCOMPLETE",
    "ERROR",
)


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
            f"/orgs/{org}/repos?per_page=100&type=all",
        ]
    )
    if not isinstance(payload, list):
        raise SystemExit("FAIL unexpected org repository inventory payload")
    records: list[RepoRecord] = []
    for item in payload:
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
        if version == target_version:
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

    if version == target_version and (not target_baseline or baseline == target_baseline):
        return RepoResult(
            full_name="",
            state="CURRENT",
            action="NONE",
            version=version,
            baseline=baseline,
            mode=mode,
            detail="managed adoption already current",
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
            raise SystemExit(f"FAIL local_path missing for {record.full_name}: {path}")
        return path

    if not record.clone_url:
        raise SystemExit(f"FAIL no clone_url or local_path for {record.full_name}")

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
        raise SystemExit(f"FAIL clone {record.full_name}: {completed.stdout.strip()}")
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


def apply_upgrade(root: Path, baseline: str) -> subprocess.CompletedProcess[str]:
    return run_cmd(
        [
            sys.executable,
            str(UPGRADE),
            "--root",
            str(root),
            "--apply",
            "--baseline-sha",
            baseline,
        ]
    )


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
) -> RepoResult:
    if record.archived and not include_archived:
        return RepoResult(
            full_name=record.full_name,
            state="ARCHIVED",
            action="SKIP_ARCHIVED",
            detail="archived repositories are excluded unless --include-archived is set",
            outcome="SKIPPED",
        )

    root = ensure_checkout(record, workdir)
    result = classify_checkout(root, target_version, target_baseline)
    result.full_name = record.full_name

    if record.archived:
        result.state = "ARCHIVED"
        if result.action == "NONE":
            result.action = "SKIP_ARCHIVED"
        result.outcome = "SKIPPED"
        result.detail = (result.detail + "; archived repository included for classification only").strip("; ")
        return result

    if result.action in {"NONE", "SKIP_ARCHIVED"}:
        result.outcome = "NO_CHANGE"
        return result

    if result.action == "NEEDS_INPUT" or result.state in {"INCOMPLETE", "ERROR"}:
        result.outcome = "NEEDS_INPUT"
        return result

    if not apply:
        result.outcome = "PLANNED"
        return result

    branch_name = f"{branch_prefix}{target_version.replace('.', '-')}"
    create_rollout_branch(root, record.default_branch, branch_name)
    result.branch = branch_name

    if result.action == "UPGRADE":
        upgraded = apply_upgrade(root, target_baseline)
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
        attempted = run_cmd(
            [
                sys.executable,
                str(ADOPT),
                "--root",
                str(root),
                "--apply",
                "--baseline-sha",
                target_baseline,
            ]
        )
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
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--audit", action="store_true", help="Explicit audit/dry-run (default)")
    parser.add_argument("--apply", action="store_true", help="Create isolated rollout branches and optional PRs")
    parser.add_argument("--create-pr", action="store_true", help="Push branch and open PR during --apply")
    parser.add_argument("--workdir", default="", help="Working directory for clones")
    parser.add_argument("--baseline-sha", default="", help="Immutable canonical baseline SHA for apply/upgrade")
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
    target_baseline = canonical_baseline(args.baseline_sha) if (apply or args.baseline_sha) else args.baseline_sha
    if apply and not target_baseline:
        raise SystemExit("FAIL --apply requires --baseline-sha or a resolvable canonical HEAD")

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
    print(f"TARGET_BASELINE={target_baseline or '<audit-unpinned>'}")
    print(f"REPO_COUNT={len(records)}")
    print(f"INCLUDE_ARCHIVED={'YES' if args.include_archived else 'NO'}")

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
        )
        print_result(result)
        results.append(result)

    return summarize(results, apply)


if __name__ == "__main__":
    raise SystemExit(main())
