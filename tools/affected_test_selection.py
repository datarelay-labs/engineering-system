#!/usr/bin/env python3
"""Deterministically select and run affected Engineering System test scenarios."""
from __future__ import annotations

import argparse
import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml


class SelectionError(ValueError):
    pass


@dataclass(frozen=True)
class Impact:
    affected_domains: frozenset[str]
    invalidated_scenarios: frozenset[str]
    unmapped_files: tuple[str, ...]
    matched_patterns: dict[str, tuple[str, ...]]


def matches(pattern: str, path: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.endswith("/**") and path.startswith(pattern[:-3].rstrip("/") + "/")
    )


def _scenario_index(manifest: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for raw in manifest.get("scenarios") or []:
        scenario = raw or {}
        sid = str(scenario.get("id") or "").strip()
        if not sid:
            raise SelectionError("scenario without id")
        if sid in index:
            raise SelectionError(f"duplicate scenario id: {sid}")
        index[sid] = scenario
    return index


def analyze_impact(manifest: dict, changed_files: list[str]) -> Impact:
    scenarios = _scenario_index(manifest)
    affected_domains: set[str] = set()
    invalidated: set[str] = set()
    unmapped: list[str] = []
    matched: dict[str, tuple[str, ...]] = {}

    for path in changed_files:
        path_domains: set[str] = set()
        path_invalidates: set[str] = set()
        patterns: list[str] = []
        for raw_pattern, raw_spec in (manifest.get("paths") or {}).items():
            pattern = str(raw_pattern)
            if not matches(pattern, path):
                continue
            spec = raw_spec or {}
            patterns.append(pattern)
            path_domains.update(str(item) for item in spec.get("domains") or [])
            path_invalidates.update(str(item) for item in spec.get("invalidates") or [])

        if not patterns:
            unmapped.append(path)
            continue

        affected_domains.update(path_domains)
        invalidated.update(path_invalidates)
        matched[path] = tuple(patterns)

    for sid in sorted(invalidated):
        scenario = scenarios.get(sid)
        if scenario is None:
            raise SelectionError(f"path invalidates unknown scenario: {sid}")
        triggers = {str(item) for item in scenario.get("triggers") or []}
        if "affected" not in triggers:
            raise SelectionError(
                f"direct invalidation target {sid} must declare trigger 'affected'"
            )

    return Impact(
        affected_domains=frozenset(affected_domains),
        invalidated_scenarios=frozenset(invalidated),
        unmapped_files=tuple(sorted(unmapped)),
        matched_patterns=matched,
    )


def select_scenarios(
    manifest: dict,
    changed_files: list[str],
    trigger: str,
    *,
    no_base: bool = False,
) -> tuple[list[dict], Impact]:
    impact = analyze_impact(manifest, changed_files)
    widen = no_base or bool(impact.unmapped_files)
    selected: list[dict] = []
    seen: set[str] = set()

    for raw in manifest.get("scenarios") or []:
        scenario = raw or {}
        sid = str(scenario.get("id") or "")
        triggers = {str(item) for item in scenario.get("triggers") or []}
        domains = {str(item) for item in scenario.get("domains") or []}
        always_for_trigger = trigger in triggers
        domain_affected = "affected" in triggers and bool(
            domains & impact.affected_domains
        )
        directly_invalidated = sid in impact.invalidated_scenarios
        conservative = widen and (trigger in triggers or "affected" in triggers)
        if always_for_trigger or domain_affected or directly_invalidated or conservative:
            if sid and sid not in seen:
                selected.append(scenario)
                seen.add(sid)

    return selected, impact


def changed_files(base: str) -> tuple[list[str], bool]:
    if not base:
        return [], True
    payload = subprocess.check_output(
        ["git", "diff", "--name-only", "--diff-filter=ACMRTUXB", f"{base}...HEAD"],
        text=True,
    )
    return [line.strip() for line in payload.splitlines() if line.strip()], False


def run_selected(manifest_path: Path, base: str, trigger: str) -> int:
    if not manifest_path.is_file():
        raise SelectionError(f"missing test manifest: {manifest_path}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    changed, no_base = changed_files(base)
    selected, impact = select_scenarios(
        manifest, changed, trigger, no_base=no_base
    )

    if no_base:
        print("AFFECTED_SELECTOR_BASE=UNAVAILABLE")
    print("CHANGED_FILES=" + (",".join(changed) if changed else "<none>"))
    print(
        "AFFECTED_DOMAINS="
        + (
            ",".join(sorted(impact.affected_domains))
            if impact.affected_domains
            else "<none>"
        )
    )
    print(
        "DIRECT_INVALIDATIONS="
        + (
            ",".join(sorted(impact.invalidated_scenarios))
            if impact.invalidated_scenarios
            else "<none>"
        )
    )
    if impact.unmapped_files:
        print("UNMAPPED_CHANGED_FILES=" + ",".join(impact.unmapped_files))
        print("AFFECTED_SELECTOR_MODE=WIDEN")
    else:
        print("AFFECTED_SELECTOR_MODE=" + ("WIDEN" if no_base else "MAPPED"))

    if not selected:
        print("SELECTED_SCENARIOS=<none>")
        print("AFFECTED_TESTS=PASS")
        return 0

    print("SELECTED_SCENARIOS=" + ",".join(str(item["id"]) for item in selected))
    setup_command = str(manifest.get("setup_command") or "").strip()
    if setup_command:
        print("=== setup ===", flush=True)
        print(f"$ {setup_command}", flush=True)
        completed = subprocess.run(["bash", "-lc", setup_command])
        if completed.returncode:
            raise SelectionError(f"setup_command exit={completed.returncode}")
        print("PASS setup", flush=True)

    for scenario in selected:
        sid = str(scenario["id"])
        command = str(scenario.get("command") or "").strip()
        if not command:
            raise SelectionError(f"scenario {sid} has no command")
        print(f"=== {sid}: {scenario.get('name', sid)} ===", flush=True)
        print(f"$ {command}", flush=True)
        completed = subprocess.run(["bash", "-lc", command])
        if completed.returncode:
            raise SelectionError(f"scenario {sid} exit={completed.returncode}")
        print(f"PASS {sid}", flush=True)

    print("AFFECTED_TESTS=PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select and run affected Engineering System scenarios"
    )
    parser.add_argument("--manifest", default=".engineering/tests.yaml")
    parser.add_argument("--base", default="")
    parser.add_argument("--trigger", default="pr")
    args = parser.parse_args()
    try:
        return run_selected(Path(args.manifest), args.base, args.trigger or "pr")
    except (SelectionError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"FAIL {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
