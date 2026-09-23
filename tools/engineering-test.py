#!/usr/bin/env python3
"""Select and optionally run the cheapest safe affected Engineering System test."""
from __future__ import annotations

import argparse
import fnmatch
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


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def resolve_base(root: Path, explicit: str) -> str:
    if explicit:
        return explicit
    symbolic = git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    for candidate in (symbolic, "origin/main", "origin/master", "main", "master", "HEAD^"):
        if candidate and git(root, "rev-parse", "--verify", "--quiet", candidate):
            return candidate
    return ""


def changed_files(root: Path, base: str) -> list[str]:
    if base:
        text = git(root, "diff", "--name-only", f"{base}...HEAD")
        if text:
            return sorted({line for line in text.splitlines() if line.strip()})
    text = git(root, "status", "--porcelain")
    return sorted({line[3:].strip().split(" -> ")[-1] for line in text.splitlines() if line[3:].strip()})


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
    try:
        completed = subprocess.run(
            ["bash", "-lc", command],
            cwd=root,
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
