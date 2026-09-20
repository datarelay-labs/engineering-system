#!/usr/bin/env python3
"""Fail-closed structural compliance check for an adopted project repository."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

REQUIRED = (
    "AGENTS.md",
    ".engineering/project.yaml",
    ".engineering/tests.yaml",
    ".engineering/release.yaml",
    ".cursor/rules/engineering-system.mdc",
)

CONTINUITY_REQUIRED = (
    ".cursor/commands/resume.md",
    ".github/ISSUE_TEMPLATE/ai-work-packet.md",
)

MANAGED_ADOPTION_REQUIRED = (
    ".github/workflows/engineering-system.yml",
)

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CANONICAL_URL = "https://github.com/datarelay-labs/engineering-system"


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def version_at_least(version: str, minimum: tuple[int, int, int]) -> bool:
    match = SEMVER_RE.fullmatch(version.strip())
    if not match:
        return False
    return tuple(int(part) for part in match.groups()) >= minimum


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    failures: list[str] = []
    for rel in REQUIRED:
        if not (root / rel).is_file():
            failures.append(f"missing required file: {rel}")

    project_path = root / ".engineering/project.yaml"
    version = ""
    mode = ""
    baseline = ""
    ci_mode = ""
    if project_path.is_file():
        try:
            project = load_yaml(project_path) or {}
            engineering = project.get("engineering_system") or {}
            version = str(engineering.get("version") or "")
            mode = str(engineering.get("mode") or "")
            baseline = str(engineering.get("baseline") or "")
            ci_mode = str(engineering.get("ci_mode") or "")
            if not version:
                failures.append("project.yaml missing engineering_system.version")
            elif not SEMVER_RE.fullmatch(version):
                failures.append(f"project.yaml has invalid engineering_system.version: {version}")
            if version_at_least(version, (1, 4, 0)):
                if mode not in {"canonical", "adopted"}:
                    failures.append("Engineering System >=1.4.0 requires engineering_system.mode=canonical|adopted")
                if mode == "adopted" and not FULL_SHA_RE.fullmatch(baseline):
                    failures.append("managed adopted repository requires immutable engineering_system.baseline SHA")
                if mode == "adopted" and ci_mode not in {"shared", "native"}:
                    failures.append("managed adopted repository requires engineering_system.ci_mode=shared|native")
        except Exception as exc:
            failures.append(f"cannot parse project.yaml: {exc}")

    if version and version_at_least(version, (1, 3, 0)):
        for rel in CONTINUITY_REQUIRED:
            if not (root / rel).is_file():
                failures.append(f"Engineering System >=1.3.0 missing session-continuity file: {rel}")

    agents_path = root / "AGENTS.md"
    if agents_path.is_file():
        agents_text = agents_path.read_text(encoding="utf-8", errors="replace")
        if CANONICAL_URL not in agents_text:
            failures.append("AGENTS.md does not reference canonical Engineering System")

    cursor_path = root / ".cursor/rules/engineering-system.mdc"
    if cursor_path.is_file():
        text = cursor_path.read_text(encoding="utf-8", errors="replace")
        if "alwaysApply: true" not in text:
            failures.append("Cursor engineering-system rule is not alwaysApply: true")
        if mode == "adopted" and "canonical Engineering System" not in text:
            failures.append("Cursor engineering-system rule does not identify canonical Engineering System")

    tests_path = root / ".engineering/tests.yaml"
    if tests_path.is_file():
        try:
            tests = load_yaml(tests_path) or {}
            scenarios = tests.get("scenarios")
            if not isinstance(scenarios, list) or not scenarios:
                failures.append("tests.yaml must contain at least one scenario")
            else:
                for scenario in scenarios:
                    if not str((scenario or {}).get("command") or "").strip():
                        failures.append("tests.yaml contains scenario without command")
                        break
        except Exception as exc:
            failures.append(f"cannot parse tests.yaml: {exc}")

    release_path = root / ".engineering/release.yaml"
    release = {}
    if release_path.is_file():
        try:
            release = load_yaml(release_path) or {}
            if bool(release.get("preflight_required")) and not str(release.get("preflight_command") or "").strip():
                failures.append("release.yaml preflight_required=true but preflight_command is empty")
        except Exception as exc:
            failures.append(f"cannot parse release.yaml: {exc}")

    if version and version_at_least(version, (1, 4, 0)) and mode == "adopted":
        for rel in MANAGED_ADOPTION_REQUIRED:
            if not (root / rel).is_file():
                failures.append(f"managed adoption missing required file: {rel}")

        workflow_path = root / ".github/workflows/engineering-system.yml"
        if workflow_path.is_file() and FULL_SHA_RE.fullmatch(baseline):
            workflow_text = workflow_path.read_text(encoding="utf-8", errors="replace")
            if f"adoption-compliance.yml@{baseline}" not in workflow_text:
                failures.append("engineering-system.yml compliance workflow is not pinned to project baseline")
            if ci_mode == "shared" and f"affected-tests.yml@{baseline}" not in workflow_text:
                failures.append("shared CI mode requires affected workflow pinned to project baseline")
            if ci_mode == "native" and f"affected-tests.yml@{baseline}" in workflow_text:
                failures.append("native CI mode must not duplicate the shared affected-tests workflow")
            if ci_mode == "native":
                other_workflows = [
                    path for path in (root / ".github/workflows").glob("*.y*ml")
                    if path.name not in {"engineering-system.yml", "engineering-release.yml"}
                ]
                if not other_workflows:
                    failures.append("native CI mode selected but no project-native workflow was found")

        qualification = str(release.get("qualification_command") or "").strip()
        if qualification:
            rel_workflow = root / ".github/workflows/engineering-release.yml"
            if not rel_workflow.is_file():
                failures.append("release qualification command configured but engineering-release.yml is missing")
            elif FULL_SHA_RE.fullmatch(baseline):
                release_text = rel_workflow.read_text(encoding="utf-8", errors="replace")
                if f"release-gate.yml@{baseline}" not in release_text:
                    failures.append("engineering-release.yml release gate is not pinned to project baseline")
                if bool(release.get("preflight_required")) and f"release-preflight.yml@{baseline}" not in release_text:
                    failures.append("engineering-release.yml preflight is not pinned to project baseline")

    if failures:
        for item in failures:
            print(f"FAIL {item}")
        print("ENGINEERING_SYSTEM_ADOPTION=FAIL")
        return 1

    print(f"ENGINEERING_SYSTEM_VERSION={version or '<unknown>'}")
    if mode:
        print(f"ENGINEERING_SYSTEM_MODE={mode}")
    if baseline:
        print(f"ENGINEERING_SYSTEM_BASELINE={baseline}")
    if ci_mode:
        print(f"ENGINEERING_SYSTEM_CI_MODE={ci_mode}")
    print("ENGINEERING_SYSTEM_ADOPTION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
