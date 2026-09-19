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

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


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
    if project_path.is_file():
        try:
            project = load_yaml(project_path) or {}
            version = str(((project.get("engineering_system") or {}).get("version")) or "")
            if not version:
                failures.append("project.yaml missing engineering_system.version")
            elif not SEMVER_RE.fullmatch(version):
                failures.append(f"project.yaml has invalid engineering_system.version: {version}")
        except Exception as exc:
            failures.append(f"cannot parse project.yaml: {exc}")

    if version and version_at_least(version, (1, 3, 0)):
        for rel in CONTINUITY_REQUIRED:
            if not (root / rel).is_file():
                failures.append(f"Engineering System >=1.3.0 missing session-continuity file: {rel}")

    cursor_path = root / ".cursor/rules/engineering-system.mdc"
    if cursor_path.is_file():
        text = cursor_path.read_text(encoding="utf-8")
        if "alwaysApply: true" not in text:
            failures.append("Cursor engineering-system rule is not alwaysApply: true")

    if failures:
        for item in failures:
            print(f"FAIL {item}")
        print("ENGINEERING_SYSTEM_ADOPTION=FAIL")
        return 1

    print("ENGINEERING_SYSTEM_ADOPTION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
