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

    project: dict = {}
    engineering: dict = {}
    operations: dict = {}
    version = ""
    mode = ""
    baseline = ""
    ci_mode = ""
    native_ci_workflows: list[str] = []
    merge_gate_status = ""
    project_domains: set[str] = set()

    project_path = root / ".engineering/project.yaml"
    if project_path.is_file():
        try:
            project = load_yaml(project_path) or {}
            engineering = project.get("engineering_system") or {}
            operations = project.get("operations") or {}
            version = str(engineering.get("version") or "")
            mode = str(engineering.get("mode") or "")
            baseline = str(engineering.get("baseline") or "")
            ci_mode = str(engineering.get("ci_mode") or "")
            native_ci_workflows = [str(item) for item in (engineering.get("native_ci_workflows") or [])]
            merge_gate_status = str(engineering.get("merge_gate_status") or "")
            project_domains = {str(item) for item in (project.get("domains") or []) if str(item).strip()}

            if not version:
                failures.append("project.yaml missing engineering_system.version")
            elif not SEMVER_RE.fullmatch(version):
                failures.append(f"project.yaml has invalid engineering_system.version: {version}")

            if not project_domains:
                failures.append("project.yaml must define at least one domain")

            if version_at_least(version, (1, 4, 0)):
                if mode not in {"canonical", "adopted"}:
                    failures.append("Engineering System >=1.4.0 requires engineering_system.mode=canonical|adopted")
                if mode == "adopted" and not FULL_SHA_RE.fullmatch(baseline):
                    failures.append("managed adopted repository requires immutable engineering_system.baseline SHA")
                if mode == "adopted" and ci_mode not in {"shared", "native"}:
                    failures.append("managed adopted repository requires engineering_system.ci_mode=shared|native")

            if version_at_least(version, (1, 5, 0)) and mode == "adopted":
                if merge_gate_status not in {"verified", "advisory", "unknown"}:
                    failures.append("Engineering System >=1.5.0 requires merge_gate_status=verified|advisory|unknown")
                for key in ("production_oriented", "runbook_required", "incident_response_required"):
                    if not isinstance(operations.get(key), bool):
                        failures.append(f"Engineering System >=1.5.0 requires operations.{key}=true|false")
                if bool(operations.get("production_oriented")):
                    if not bool(operations.get("runbook_required")):
                        failures.append("production-oriented adoption requires operations.runbook_required=true")
                    if not bool(operations.get("incident_response_required")):
                        failures.append("production-oriented adoption requires operations.incident_response_required=true")

            if version_at_least(version, (1, 6, 0)) and mode == "adopted":
                if not isinstance(operations.get("persistent_state"), bool):
                    failures.append("Engineering System >=1.6.0 requires operations.persistent_state=true|false")
                runbooks = operations.get("runbook_paths")
                if not isinstance(runbooks, list):
                    failures.append("Engineering System >=1.6.0 requires operations.runbook_paths list")
                    runbooks = []
                for key in (
                    "health_command",
                    "backup_command",
                    "restore_test_command",
                    "upgrade_command",
                    "rollback_command",
                ):
                    if not isinstance(operations.get(key), str):
                        failures.append(f"Engineering System >=1.6.0 requires operations.{key} string")

                if bool(operations.get("production_oriented")):
                    if not runbooks:
                        failures.append("production-oriented adoption requires at least one operations.runbook_paths entry")
                    for rel in runbooks:
                        if not (root / str(rel)).is_file():
                            failures.append(f"production runbook path missing: {rel}")
                    if not str(operations.get("health_command") or "").strip():
                        failures.append("production-oriented adoption requires operations.health_command")

                if bool(operations.get("persistent_state")):
                    if not str(operations.get("backup_command") or "").strip():
                        failures.append("persistent-state adoption requires operations.backup_command")
                    if not str(operations.get("restore_test_command") or "").strip():
                        failures.append("persistent-state adoption requires operations.restore_test_command")
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
        if version_at_least(version, (1, 5, 0)):
            if "standards/DESIGN.md" not in agents_text:
                failures.append("AGENTS.md missing minimal design-gate routing")
            if "standards/OPERATIONS.md" not in agents_text:
                failures.append("AGENTS.md missing incident/operations routing")
        if "tools/knowledge-contract.py" in agents_text:
            for rel in (
                "tools/knowledge-contract.py",
                "schemas/knowledge-index.schema.json",
            ):
                if not (root / rel).is_file():
                    failures.append(
                        f"AGENTS.md references knowledge contract but missing {rel}"
                    )
        if "tools/runtime-contract.py" in agents_text:
            for rel in (
                "tools/runtime-contract.py",
                "schemas/runtime-contract.schema.json",
            ):
                if not (root / rel).is_file():
                    failures.append(
                        f"AGENTS.md references runtime contract but missing {rel}"
                    )

    if version_at_least(version, (1, 6, 4)) and not (root / ".cursorignore").is_file():
        failures.append("Engineering System >=1.6.4 adoption requires .cursorignore")

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
            paths = tests.get("paths") or {}
            for pattern, spec in paths.items():
                for domain in (spec or {}).get("domains") or []:
                    if project_domains and str(domain) not in project_domains:
                        failures.append(f"tests.yaml path {pattern} references unknown domain: {domain}")

            scenarios = tests.get("scenarios")
            if not isinstance(scenarios, list) or not scenarios:
                failures.append("tests.yaml must contain at least one scenario")
            else:
                for scenario in scenarios:
                    scenario = scenario or {}
                    if not str(scenario.get("command") or "").strip():
                        failures.append("tests.yaml contains scenario without command")
                        break
                    for domain in scenario.get("domains") or []:
                        if project_domains and str(domain) not in project_domains:
                            failures.append(
                                f"tests.yaml scenario {scenario.get('id', '<unknown>')} references unknown domain: {domain}"
                            )
        except Exception as exc:
            failures.append(f"cannot parse tests.yaml: {exc}")

    release_path = root / ".engineering/release.yaml"
    release: dict = {}
    if release_path.is_file():
        try:
            release = load_yaml(release_path) or {}
            if bool(release.get("preflight_required")) and not str(release.get("preflight_command") or "").strip():
                failures.append("release.yaml preflight_required=true but preflight_command is empty")
            if version_at_least(version, (1, 5, 0)) and bool(operations.get("production_oriented")):
                if not bool(release.get("operational_e2e_required")):
                    failures.append("production-oriented adoption requires operational_e2e_required=true")
                if int(release.get("full_e2e_passes") or 0) < 1:
                    failures.append("production-oriented adoption requires full_e2e_passes>=1")
                if not bool(release.get("public_smoke_required")):
                    failures.append("production-oriented adoption requires public_smoke_required=true")

            if version_at_least(version, (1, 6, 0)) and mode == "adopted":
                for key in (
                    "setup_command",
                    "preflight_command",
                    "qualification_command",
                    "artifact_hash_command",
                    "provenance_command",
                    "sbom_command",
                    "operational_e2e_command",
                    "public_smoke_command",
                ):
                    if not isinstance(release.get(key), str):
                        failures.append(f"Engineering System >=1.6.0 requires release.{key} string")

                required_commands = (
                    ("artifact_hash_required", "artifact_hash_command"),
                    ("provenance_required", "provenance_command"),
                    ("sbom_required", "sbom_command"),
                    ("operational_e2e_required", "operational_e2e_command"),
                    ("public_smoke_required", "public_smoke_command"),
                )
                for flag, command_key in required_commands:
                    if bool(release.get(flag)) and not str(release.get(command_key) or "").strip():
                        failures.append(f"{flag}=true requires {command_key}")
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
            if version_at_least(version, (1, 6, 0)) and f"enforcement-check.yml@{baseline}" not in workflow_text:
                failures.append("Engineering System >=1.6.0 requires enforcement reconciliation pinned to project baseline")

        if ci_mode == "native":
            if version_at_least(version, (1, 5, 0)):
                if not native_ci_workflows:
                    failures.append("native CI mode requires explicit native_ci_workflows mapping")
                for rel in native_ci_workflows:
                    if not (root / rel).is_file():
                        failures.append(f"mapped native CI workflow missing: {rel}")
            else:
                other_workflows = [
                    path for path in (root / ".github/workflows").glob("*.y*ml")
                    if path.name not in {"engineering-system.yml", "engineering-release.yml"}
                ]
                if not other_workflows:
                    failures.append("native CI mode selected but no project-native workflow was found")

        qualification = str(release.get("qualification_command") or "").strip()
        release_contract_commands = [
            qualification,
            str(release.get("setup_command") or "").strip(),
            str(release.get("preflight_command") or "").strip(),
            str(release.get("artifact_hash_command") or "").strip(),
            str(release.get("provenance_command") or "").strip(),
            str(release.get("sbom_command") or "").strip(),
            str(release.get("operational_e2e_command") or "").strip(),
            str(release.get("public_smoke_command") or "").strip(),
        ]
        if version_at_least(version, (1, 6, 0)):
            if any(release_contract_commands):
                rel_workflow = root / ".github/workflows/engineering-release.yml"
                if not rel_workflow.is_file():
                    failures.append("release contract configured but engineering-release.yml is missing")
                elif FULL_SHA_RE.fullmatch(baseline):
                    release_text = rel_workflow.read_text(encoding="utf-8", errors="replace")
                    if f"release-contract.yml@{baseline}" not in release_text:
                        failures.append("engineering-release.yml release contract is not pinned to project baseline")
        elif qualification:
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
    if native_ci_workflows:
        print("NATIVE_CI_WORKFLOWS=" + ",".join(native_ci_workflows))
    if merge_gate_status:
        print(f"MERGE_GATE_ENFORCEMENT={merge_gate_status}")
    if operations:
        print(
            "OPERATIONS_PROFILE="
            + ("production" if operations.get("production_oriented") else "nonproduction")
        )
    print("ENGINEERING_SYSTEM_ADOPTION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
