#!/usr/bin/env python3
"""Select and optionally run the cheapest safe affected Engineering System test."""
from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
from pathlib import Path

import yaml

COST_RANK = {"cheap": 0, "medium": 1, "expensive": 2}
LEVEL_COST = {
    "static": "cheap",
    "unit": "cheap",
    "component": "medium",
    "feature": "medium",
    "integration": "expensive",
    "ux": "expensive",
    "lifecycle": "expensive",
    "performance": "expensive",
    "e2e": "expensive",
}
METADATA_PATHS = {
    "AGENTS.md",
    ".cursorignore",
    ".engineering/project.yaml",
    ".engineering/tests.yaml",
    ".engineering/release.yaml",
    ".cursor/rules/engineering-system.mdc",
    ".cursor/commands/resume.md",
    ".cursor/commands/work-resume.md",
    ".github/ISSUE_TEMPLATE/ai-work-packet.md",
    ".github/workflows/engineering-system.yml",
    ".github/workflows/engineering-release.yml",
}


def fail_selection(message: str) -> None:
    raise SystemExit(f"TEST_SELECTION=FAIL {message}")


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(args=["git", *args], returncode=127, stdout="")


def run_git_bytes(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(args=["git", *args], returncode=127, stdout=b"")


def decode_git_path(raw: bytes) -> str:
    return raw.decode("utf-8", errors="surrogateescape")


def nul_records(payload: bytes) -> list[bytes]:
    if not payload:
        return []
    records = payload.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    return records


def parse_status_z(payload: bytes) -> set[str]:
    found: set[str] = set()
    records = nul_records(payload)
    index = 0
    while index < len(records):
        entry = records[index]
        if len(entry) < 4 or entry[2:3] != b" ":
            fail_selection("malformed git status output")
        status = entry[:2]
        paths = [decode_git_path(entry[3:])]
        index += 1
        if b"R" in status or b"C" in status:
            if index >= len(records):
                fail_selection("malformed git status output")
            paths.append(decode_git_path(records[index]))
            index += 1
        found.update(path for path in paths if path)
    return found


def parse_name_status_z(payload: bytes) -> set[str]:
    found: set[str] = set()
    records = nul_records(payload)
    index = 0
    while index < len(records):
        status = records[index]
        if not status:
            fail_selection("malformed git diff output")
        path_count = 2 if status[:1] in (b"R", b"C") else 1
        if index + 1 + path_count > len(records):
            fail_selection("malformed git diff output")
        for offset in range(1, 1 + path_count):
            path = decode_git_path(records[index + offset])
            if path:
                found.add(path)
        index += 1 + path_count
    return found


def git(root: Path, *args: str) -> str:
    completed = run_git(root, *args)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def resolve_base(root: Path, explicit: str) -> str:
    if explicit:
        if not git(root, "rev-parse", "--verify", "--quiet", f"{explicit}^{{commit}}"):
            fail_selection(f"unresolved base: {explicit}")
        return explicit
    symbolic = git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    for candidate in (symbolic, "origin/main", "origin/master", "main", "master", "HEAD^"):
        if candidate and git(root, "rev-parse", "--verify", "--quiet", candidate):
            return candidate
    return ""


def committed_paths(root: Path, base: str) -> set[str]:
    completed = run_git_bytes(root, "diff", "-z", "--name-status", "--find-renames", f"{base}...HEAD")
    if completed.returncode != 0:
        fail_selection(f"git diff failed for base: {base}")
    return parse_name_status_z(completed.stdout or b"")


def worktree_paths(root: Path) -> set[str]:
    completed = run_git_bytes(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if completed.returncode != 0:
        fail_selection("git status failed")
    return parse_status_z(completed.stdout or b"")


def changed_files(root: Path, base: str) -> list[str]:
    found: set[str] = set()
    if base:
        found.update(committed_paths(root, base))
    found.update(worktree_paths(root))
    return sorted(found)


def matches(pattern: str, path: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or (
        pattern.endswith("/**") and path.startswith(pattern[:-3].rstrip("/") + "/")
    )


def affected_domains(data: dict, files: list[str]) -> set[str]:
    domains: set[str] = set()
    for pattern, spec in (data.get("paths") or {}).items():
        if any(matches(str(pattern), path) for path in files):
            domains.update(str(item) for item in (spec or {}).get("domains") or [])
    return domains


def scenario_cost(scenario: dict) -> str:
    explicit = str(scenario.get("cost") or "").strip()
    if explicit in COST_RANK:
        return explicit
    return LEVEL_COST.get(str(scenario.get("level") or "").strip(), "expensive")


def is_metadata_only(files: list[str]) -> bool:
    return bool(files) and all(path in METADATA_PATHS for path in files)


def select_scenario(data: dict, domains: set[str], scenario_id: str) -> dict | None:
    scenarios = [item or {} for item in data.get("scenarios") or []]
    if scenario_id:
        for item in scenarios:
            if str(item.get("id") or "") == scenario_id:
                return item
        return None

    candidates = []
    for item in scenarios:
        if item.get("agent_default") is False:
            continue
        item_domains = {str(value) for value in item.get("domains") or []}
        if domains and item_domains and not (domains & item_domains):
            continue
        cost = scenario_cost(item)
        estimate = int(item.get("estimated_seconds") or 10**9)
        candidates.append((COST_RANK[cost], estimate, str(item.get("id") or ""), item))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    return candidates[0][3]


def main() -> int:
    parser = argparse.ArgumentParser(description="Select the cheapest safe affected test")
    parser.add_argument("--root", default=".")
    parser.add_argument("--base", default="")
    parser.add_argument("--scenario", default="")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--allow-expensive-metadata", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    manifest = root / ".engineering/tests.yaml"
    if not manifest.is_file():
        raise SystemExit("TEST_SELECTION=FAIL missing .engineering/tests.yaml")
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    base = resolve_base(root, args.base)
    files = changed_files(root, base)
    domains = affected_domains(data, files)
    scenario = select_scenario(data, domains, args.scenario)
    if not scenario:
        print("TEST_SELECTION=NONE")
        return 0

    sid = str(scenario.get("id") or "<unknown>")
    cost = scenario_cost(scenario)
    command = str(scenario.get("command") or "").strip()
    if not command:
        raise SystemExit(f"TEST_SELECTION=FAIL scenario {sid} has no command")

    print(f"TEST_BASE={base or '<none>'}")
    print("TEST_DOMAINS=" + (",".join(sorted(domains)) if domains else "<none>"))
    print(f"TEST_SELECTED={sid}")
    print(f"TEST_COST={cost}")
    print(f"TEST_SCOPE={scenario.get('scope') or scenario.get('level') or '<unknown>'}")
    print(f"TEST_COMMAND={command}")

    if is_metadata_only(files) and cost == "expensive" and not args.allow_expensive_metadata:
        print("TEST_SELECTION=SKIP_EXPENSIVE_METADATA_ONLY")
        return 0
    if not args.run:
        print("TEST_SELECTION=PLAN")
        return 0

    timeout = int(scenario.get("timeout_seconds") or 600)
    log_dir = root / ".engineering" / "agent-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{sid}.log"
    child_env = os.environ.copy()
    if base:
        child_env["ENGINEERING_BASE_REF"] = base
    else:
        child_env.pop("ENGINEERING_BASE_REF", None)
    try:
        completed = subprocess.run(
            ["bash", "-lc", command],
            cwd=root,
            env=child_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        log_path.write_text(output, encoding="utf-8")
        print(f"TEST_LOG={log_path.relative_to(root)}")
        print(f"TEST_RESULT=TIMEOUT timeout_seconds={timeout}")
        return 124

    log_path.write_text(completed.stdout or "", encoding="utf-8")
    print(f"TEST_LOG={log_path.relative_to(root)}")
    if completed.returncode == 0:
        print("TEST_RESULT=PASS")
        return 0

    tail = (completed.stdout or "").splitlines()[-40:]
    print(f"TEST_RESULT=FAIL exit_code={completed.returncode}")
    if tail:
        print("TEST_FAILURE_TAIL_BEGIN")
        print("\n".join(tail))
        print("TEST_FAILURE_TAIL_END")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
