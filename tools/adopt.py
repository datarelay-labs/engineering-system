#!/usr/bin/env python3
"""Deterministic audit/bootstrap helper for Engineering System adoption.

The tool automates mechanical installation only. It never overwrites existing
project files and never decides that project-specific rules are obsolete.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

CANONICAL = Path(__file__).resolve().parents[1]
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

RULE_SURFACES = (
    "AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules",
    ".cursor/commands",
    ".github/copilot-instructions.md",
)

REQUIRED_MANAGED = (
    "AGENTS.md",
    ".engineering/project.yaml",
    ".engineering/tests.yaml",
    ".engineering/release.yaml",
    ".cursor/rules/engineering-system.mdc",
    ".cursor/commands/resume.md",
    ".github/ISSUE_TEMPLATE/ai-work-packet.md",
    ".github/workflows/engineering-system.yml",
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


def canonical_version() -> str:
    return (CANONICAL / "VERSION").read_text(encoding="utf-8").strip()


def canonical_baseline(explicit: str) -> str:
    if explicit:
        baseline = explicit.strip()
    else:
        baseline = run_git(CANONICAL, "rev-parse", "HEAD")
    if not FULL_SHA_RE.fullmatch(baseline):
        raise SystemExit(
            "FAIL canonical baseline SHA unavailable; pass --baseline-sha with a 40-character commit SHA"
        )
    return baseline


def git_state(root: Path) -> dict[str, str | bool]:
    top = run_git(root, "rev-parse", "--show-toplevel")
    return {
        "is_git_repo": bool(top),
        "root": top or str(root),
        "origin": run_git(root, "remote", "get-url", "origin"),
        "branch": run_git(root, "branch", "--show-current"),
        "head": run_git(root, "rev-parse", "HEAD"),
        "dirty": bool(run_git(root, "status", "--porcelain")),
    }


def detect_project_type(root: Path) -> str:
    types: list[str] = []
    if (root / "pyproject.toml").is_file() or (root / "requirements.txt").is_file():
        types.append("python")
    if (root / "package.json").is_file():
        types.append("node")
    if (root / "go.mod").is_file():
        types.append("go")
    if (root / "Cargo.toml").is_file():
        types.append("rust")
    if (root / "pom.xml").is_file() or (root / "mvnw").is_file():
        types.append("maven")
    if (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file() or (root / "gradlew").is_file():
        types.append("gradle")
    if len(types) > 1:
        return "mixed-" + "-".join(types)
    return types[0] if types else "generic"


def node_test_command(root: Path) -> str:
    package = root / "package.json"
    if not package.is_file():
        return ""
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except Exception:
        return ""
    test = str(((data.get("scripts") or {}).get("test")) or "").strip()
    if not test or "no test specified" in test.lower():
        return ""
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm test"
    if (root / "yarn.lock").is_file():
        return "yarn test"
    return "npm test"


def discover_test_commands(root: Path) -> list[str]:
    commands: list[str] = []

    explicit_runners = (
        ("tests/run-all.sh", "bash tests/run-all.sh"),
        ("tests/run.sh", "bash tests/run.sh"),
        ("test/run-all.sh", "bash test/run-all.sh"),
    )
    for rel, command in explicit_runners:
        if (root / rel).is_file():
            commands.append(command)

    if (root / "go.mod").is_file():
        commands.append("go test ./...")
    if (root / "Cargo.toml").is_file():
        commands.append("cargo test")
    if (root / "mvnw").is_file():
        commands.append("./mvnw test")
    elif (root / "pom.xml").is_file():
        commands.append("mvn test")
    if (root / "gradlew").is_file():
        commands.append("./gradlew test")
    elif (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        commands.append("gradle test")

    node = node_test_command(root)
    if node:
        commands.append(node)

    python_markers = (
        root / "pytest.ini",
        root / "pyproject.toml",
        root / "setup.cfg",
    )
    if (root / "tests").is_dir() and any(p.exists() for p in python_markers):
        commands.append("python -m pytest -q")

    # Preserve order while removing duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for command in commands:
        if command not in seen:
            seen.add(command)
            unique.append(command)
    return unique


def source_patterns(root: Path) -> list[str]:
    candidates = ("src", "lib", "app", "cmd", "pkg", "internal", "server", "client", "tests", "test")
    patterns = [f"{name}/**" for name in candidates if (root / name).exists()]
    if not patterns:
        patterns = ["**"]
    return patterns


def rule_surfaces(root: Path) -> list[str]:
    found: list[str] = []
    for rel in RULE_SURFACES:
        path = root / rel
        if path.is_file():
            found.append(rel)
        elif path.is_dir():
            for item in sorted(path.rglob("*")):
                if item.is_file():
                    found.append(str(item.relative_to(root)))
    return found


def review_required_rules(root: Path, rules: list[str]) -> list[str]:
    exact_generated = {
        "AGENTS.md": CANONICAL / "templates" / "AGENTS.md",
        ".cursor/rules/engineering-system.mdc": CANONICAL / "templates" / ".cursor" / "rules" / "engineering-system.mdc",
        ".cursor/commands/resume.md": CANONICAL / "templates" / ".cursor" / "commands" / "resume.md",
    }
    required: list[str] = []
    for rel in rules:
        source = exact_generated.get(rel)
        target = root / rel
        if source and source.is_file() and target.is_file():
            if source.read_text(encoding="utf-8") == target.read_text(encoding="utf-8", errors="replace"):
                continue
        required.append(rel)
    return required


def existing_ci(root: Path) -> list[str]:
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return []
    return [
        str(path.relative_to(root))
        for path in sorted(workflow_dir.iterdir())
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    ]


def inventory(root: Path) -> dict[str, object]:
    return {
        "git": git_state(root),
        "project_type": detect_project_type(root),
        "test_candidates": discover_test_commands(root),
        "source_patterns": source_patterns(root),
        "existing_rule_surfaces": rule_surfaces(root),
        "existing_ci": existing_ci(root),
        "existing_adoption_files": [rel for rel in REQUIRED_MANAGED if (root / rel).is_file()],
    }


def print_inventory(data: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return

    git = data["git"]
    assert isinstance(git, dict)
    print(f"TARGET_ROOT={git.get('root', '')}")
    print(f"TARGET_ORIGIN={git.get('origin', '') or '<none>'}")
    print(f"TARGET_BRANCH={git.get('branch', '') or '<none>'}")
    print(f"TARGET_HEAD={git.get('head', '') or '<none>'}")
    print(f"WORKTREE_DIRTY={'YES' if git.get('dirty') else 'NO'}")
    print(f"PROJECT_TYPE={data['project_type']}")
    tests = data["test_candidates"]
    rules = data["existing_rule_surfaces"]
    ci = data["existing_ci"]
    print("TEST_CANDIDATES=" + (" | ".join(tests) if tests else "<none>"))
    print("RULE_SURFACES=" + (",".join(rules) if rules else "<none>"))
    print("CI_WORKFLOWS=" + (",".join(ci) if ci else "<none>"))
    print("ADOPTION_AUDIT=PASS")


def yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def project_yaml(root: Path, version: str, baseline: str, project_type: str, maturity: str, domain: str, platform: str) -> str:
    return (
        "engineering_system:\n"
        f"  version: {yaml_scalar(version)}\n"
        "  mode: adopted\n"
        f"  baseline: {yaml_scalar(baseline)}\n\n"
        "project:\n"
        f"  name: {yaml_scalar(root.name)}\n"
        f"  type: {yaml_scalar(project_type)}\n"
        f"  maturity: {yaml_scalar(maturity)}\n\n"
        "domains:\n"
        f"  - {yaml_scalar(domain)}\n\n"
        "platforms:\n"
        f"  - {yaml_scalar(platform)}\n\n"
        "operations:\n"
        "  production_oriented: false\n"
        "  runbook_required: false\n"
    )


def tests_yaml(patterns: list[str], test_command: str, domain: str, platform: str) -> str:
    lines = ["version: 1", "", "paths:"]
    for pattern in patterns:
        lines.extend(
            [
                f"  {yaml_scalar(pattern)}:",
                "    domains:",
                f"      - {yaml_scalar(domain)}",
            ]
        )
    lines.extend(["", "scenarios:"])
    if test_command:
        lines.extend(
            [
                "  - id: ADOPTED-TEST-001",
                "    name: Project-native affected tests",
                "    level: integration",
                "    domains:",
                f"      - {yaml_scalar(domain)}",
                "    triggers:",
                "      - affected",
                "    platforms:",
                f"      - {yaml_scalar(platform)}",
                f"    command: {yaml_scalar(test_command)}",
                "    invariants:",
                '      - "project-native tests remain green for affected changes"',
                "    release_gate: true",
                "",
            ]
        )
    lines.extend(
        [
            "  - id: ADOPTED-STATIC-001",
            "    name: Git whitespace validation",
            "    level: static",
            "    domains:",
            f"      - {yaml_scalar(domain)}",
            "    triggers:",
            "      - pr",
            "      - preflight",
            "      - release",
            "    platforms:",
            f"      - {yaml_scalar(platform)}",
            '    command: "git diff --check"',
            "    invariants:",
            '      - "repository diff has no whitespace errors"',
            "    release_gate: true",
            "",
        ]
    )
    return "\n".join(lines)


def release_yaml(preflight_command: str, release_command: str) -> str:
    return (
        "version: 1\n\n"
        "exact_head_required: true\n"
        "artifact_hash_required: false\n"
        "provenance_required: false\n"
        "sbom_required: false\n"
        f"preflight_required: {'true' if preflight_command else 'false'}\n"
        f"preflight_command: {yaml_scalar(preflight_command)}\n"
        f"qualification_command: {yaml_scalar(release_command)}\n"
        "operational_e2e_required: false\n"
        "full_e2e_passes: 0\n"
        "public_smoke_required: false\n\n"
        "blockers:\n"
        "  p0: true\n"
        "  p1: true\n"
        "  user_blocking_p2: true\n"
    )


def engineering_workflow(baseline: str) -> str:
    return f"""name: Engineering System

on:
  pull_request:

permissions:
  contents: read

jobs:
  adoption-compliance:
    uses: datarelay-labs/engineering-system/.github/workflows/adoption-compliance.yml@{baseline}

  affected-tests:
    uses: datarelay-labs/engineering-system/.github/workflows/affected-tests.yml@{baseline}
    with:
      manifest_path: .engineering/tests.yaml
      trigger: pr
"""


def release_workflow(baseline: str, preflight_command: str, release_command: str) -> str:
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
                f"      preflight_command: {yaml_scalar(preflight_command)}",
                "",
            ]
        )
    lines.extend(
        [
            "  release-gate:",
        ]
    )
    if preflight_command:
        lines.append("    needs: preflight")
    lines.extend(
        [
            f"    uses: datarelay-labs/engineering-system/.github/workflows/release-gate.yml@{baseline}",
            "    with:",
            f"      expected_sha: {expression}",
            f"      qualification_command: {yaml_scalar(release_command)}",
            "",
        ]
    )
    return "\n".join(lines)


def write_missing(root: Path, rel: str, content: str, written: list[str], skipped: list[str]) -> None:
    path = root / rel
    if path.exists():
        skipped.append(rel)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    written.append(rel)


def ensure_no_existing_managed_upgrade(root: Path, version: str) -> None:
    project = root / ".engineering" / "project.yaml"
    if not project.is_file():
        return
    text = project.read_text(encoding="utf-8", errors="replace")
    if version not in text:
        raise SystemExit(
            "FAIL existing Engineering System adoption detected at another version; audit and upgrade it deliberately instead of using bootstrap apply"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit or bootstrap Engineering System adoption")
    parser.add_argument("--root", required=True)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--ack-rule-review", action="store_true")
    parser.add_argument("--allow-no-tests", action="store_true")
    parser.add_argument("--test-command", default="")
    parser.add_argument("--release-command", default="")
    parser.add_argument("--preflight-command", default="")
    parser.add_argument("--baseline-sha", default="")
    parser.add_argument("--project-type", default="")
    parser.add_argument("--maturity", default="development", choices=("experimental", "development", "production", "maintenance"))
    parser.add_argument("--domain", default="core")
    parser.add_argument("--platform", default="linux")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"FAIL target root does not exist: {root}")

    data = inventory(root)
    if args.audit or not args.apply:
        print_inventory(data, args.json)
        if not args.apply:
            return 0

    git = data["git"]
    assert isinstance(git, dict)
    if not git.get("is_git_repo"):
        raise SystemExit("FAIL target must be a Git repository")
    if git.get("dirty") and not args.allow_dirty:
        raise SystemExit("FAIL target worktree is dirty; preserve unrelated work or pass --allow-dirty after explicit review")

    rules = list(data["existing_rule_surfaces"])
    preexisting_rules = review_required_rules(root, rules)
    if preexisting_rules and not args.ack_rule_review:
        print("RULE_REVIEW_REQUIRED=" + ",".join(preexisting_rules))
        raise SystemExit("FAIL classify existing rules before apply; rerun with --ack-rule-review after review")

    version = canonical_version()
    ensure_no_existing_managed_upgrade(root, version)
    baseline = canonical_baseline(args.baseline_sha)

    candidates = list(data["test_candidates"])
    test_command = args.test_command.strip()
    if not test_command:
        if len(candidates) == 1:
            test_command = candidates[0]
        elif len(candidates) > 1:
            print("TEST_CANDIDATES=" + " | ".join(candidates))
            raise SystemExit("FAIL multiple test commands discovered; pass --test-command explicitly")
        elif not args.allow_no_tests:
            raise SystemExit("FAIL no unambiguous test command found; pass --test-command or --allow-no-tests")

    if args.preflight_command and not args.release_command:
        raise SystemExit("FAIL --preflight-command requires --release-command")

    project_type = args.project_type.strip() or str(data["project_type"])
    patterns = list(data["source_patterns"])

    written: list[str] = []
    skipped: list[str] = []

    write_missing(root, "AGENTS.md", (CANONICAL / "templates" / "AGENTS.md").read_text(encoding="utf-8"), written, skipped)
    write_missing(
        root,
        ".cursor/rules/engineering-system.mdc",
        (CANONICAL / "templates" / ".cursor" / "rules" / "engineering-system.mdc").read_text(encoding="utf-8"),
        written,
        skipped,
    )
    write_missing(
        root,
        ".cursor/commands/resume.md",
        (CANONICAL / "templates" / ".cursor" / "commands" / "resume.md").read_text(encoding="utf-8"),
        written,
        skipped,
    )
    write_missing(
        root,
        ".github/ISSUE_TEMPLATE/ai-work-packet.md",
        (CANONICAL / "templates" / ".github" / "ISSUE_TEMPLATE" / "ai-work-packet.md").read_text(encoding="utf-8"),
        written,
        skipped,
    )
    write_missing(
        root,
        ".engineering/project.yaml",
        project_yaml(root, version, baseline, project_type, args.maturity, args.domain, args.platform),
        written,
        skipped,
    )
    write_missing(
        root,
        ".engineering/tests.yaml",
        tests_yaml(patterns, test_command, args.domain, args.platform),
        written,
        skipped,
    )
    write_missing(
        root,
        ".engineering/release.yaml",
        release_yaml(args.preflight_command.strip(), args.release_command.strip()),
        written,
        skipped,
    )
    write_missing(
        root,
        ".github/workflows/engineering-system.yml",
        engineering_workflow(baseline),
        written,
        skipped,
    )
    if args.release_command.strip():
        write_missing(
            root,
            ".github/workflows/engineering-release.yml",
            release_workflow(baseline, args.preflight_command.strip(), args.release_command.strip()),
            written,
            skipped,
        )

    checker = CANONICAL / "tools" / "check-adoption.py"
    result = subprocess.run([sys.executable, str(checker), "--root", str(root)])
    if result.returncode:
        raise SystemExit(result.returncode)

    print(f"ENGINEERING_SYSTEM_VERSION={version}")
    print(f"ENGINEERING_SYSTEM_BASELINE={baseline}")
    print("FILES_WRITTEN=" + (",".join(written) if written else "<none>"))
    print("FILES_PRESERVED=" + (",".join(skipped) if skipped else "<none>"))
    if test_command:
        print(f"TEST_COMMAND={test_command}")
    else:
        print("TEST_COMMAND=<explicitly-none>")
    if args.release_command:
        print("RELEASE_AUTOMATION=WIRED")
    else:
        print("RELEASE_AUTOMATION=NOT_APPLICABLE_OR_PENDING_EXPLICIT_COMMAND")
    print("ADOPTION_BOOTSTRAP=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
