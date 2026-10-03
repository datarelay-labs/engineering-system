#!/usr/bin/env python3
"""Fail-closed structural compliance check for an adopted project repository."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

import ci_policy_audit
from adopt import (
    AGENT_RUNTIME_MANAGED,
    EXECUTION_PROFILE_MANAGED,
    USER_ACCEPTANCE_MANAGED,
    canonical_execution_policy_line,
    canonical_managed_policy_lines,
    retired_agent_artifact_paths,
    retired_agent_rules_present,
)

REQUIRED = (
    "AGENTS.md",
    ".engineering/project.yaml",
    ".engineering/tests.yaml",
    ".engineering/release.yaml",
)

CONTINUITY_REQUIRED = (
    ".github/ISSUE_TEMPLATE/ai-work-packet.md",
)

MANAGED_ADOPTION_REQUIRED = (
    ".github/workflows/engineering-system.yml",
    ".engineering/requirements-engineering-system.txt",
    "tools/governance_floor.py",
    *EXECUTION_PROFILE_MANAGED,
    *USER_ACCEPTANCE_MANAGED,
    *AGENT_RUNTIME_MANAGED,
)

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CANONICAL_URL = "https://github.com/datarelay-labs/engineering-system"
def canonical_checker_version() -> str:
    """Return the version shipped with this checker, not target-controlled data."""
    path = Path(__file__).resolve().parents[1] / ".engineering" / "project.yaml"
    try:
        payload = load_yaml(path) or {}
    except Exception:
        return ""
    engineering = payload.get("engineering_system") or {}
    return str(engineering.get("version") or "")


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def repository_relative_contract(root: Path, value: object) -> tuple[str, Path | None]:
    if not isinstance(value, str):
        return "invalid", None
    raw = value.strip()
    if not raw:
        return "empty", None
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        return "invalid", None
    try:
        candidate = (root / relative).resolve(strict=False)
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return "invalid", None
    if not candidate.is_file():
        return "missing", candidate
    return "ok", candidate


def version_at_least(version: str, minimum: tuple[int, int, int]) -> bool:
    match = SEMVER_RE.fullmatch(version.strip())
    if not match:
        return False
    return tuple(int(part) for part in match.groups()) >= minimum


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--expected-baseline", default="")
    parser.add_argument("--expected-mode", choices=("canonical", "adopted"), default="")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    failures: list[str] = []
    for rel in REQUIRED:
        if not (root / rel).is_file():
            failures.append(f"missing required file: {rel}")

    project: dict = {}
    engineering: dict = {}
    project_meta: dict = {}
    operations: dict = {}
    version = ""
    mode = ""
    baseline = ""
    policy_epoch = 0
    ci_mode = ""
    native_ci_workflows: list[str] = []
    merge_gate_status = ""
    project_domains: set[str] = set()
    checker_version = canonical_checker_version()
    checker_managed_profile = version_at_least(checker_version, (1, 6, 5))

    project_path = root / ".engineering/project.yaml"
    if project_path.is_file():
        try:
            project = load_yaml(project_path) or {}
            engineering = project.get("engineering_system") or {}
            project_meta = project.get("project") or {}
            operations = project.get("operations") or {}
            version = str(engineering.get("version") or "")
            mode = str(engineering.get("mode") or "")
            baseline = str(engineering.get("baseline") or "")
            if args.expected_mode and mode != args.expected_mode:
                failures.append(
                    f"engineering_system.mode does not match expected mode: {args.expected_mode}"
                )
            if args.expected_baseline:
                if FULL_SHA_RE.fullmatch(args.expected_baseline) is None:
                    failures.append("expected baseline is not an immutable SHA")
                elif baseline != args.expected_baseline:
                    failures.append("engineering_system.baseline does not match expected baseline")
            policy_epoch = engineering.get("policy_epoch", 0)
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

            user_facing = bool(project_meta.get("user_facing"))
            primary_user_surface = str(project_meta.get("primary_user_surface") or "none")
            allowed_user_surfaces = {"none", "browser", "cli", "desktop", "mobile", "mixed", "other"}
            if primary_user_surface not in allowed_user_surfaces:
                failures.append(f"project.yaml has unsupported project.primary_user_surface: {primary_user_surface}")
            if user_facing and primary_user_surface == "none":
                failures.append("user-facing project requires project.primary_user_surface")
            if not user_facing and primary_user_surface != "none":
                failures.append("project.primary_user_surface requires project.user_facing=true")

            if version_at_least(version, (1, 7, 0)):
                if not isinstance(policy_epoch, int) or isinstance(policy_epoch, bool) or policy_epoch < 1:
                    failures.append("Engineering System >=1.7.0 requires engineering_system.policy_epoch>=1")

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
        if mode == "adopted":
            try:
                execution_policy = canonical_execution_policy_line()
                managed_policy_lines = canonical_managed_policy_lines()
            except SystemExit as exc:
                failures.append(str(exc))
            else:
                if execution_policy not in agents_text:
                    failures.append("AGENTS.md missing managed continuous-execution policy")
                for managed_policy in managed_policy_lines:
                    if managed_policy == execution_policy:
                        continue
                    if managed_policy not in agents_text:
                        failures.append("AGENTS.md missing managed execution-authority policy")
                        break
            if retired_agent_rules_present(agents_text):
                failures.append("AGENTS.md contains retired runtime compatibility rules")
        if version_at_least(version, (1, 5, 0)):
            if "standards/DESIGN.md" not in agents_text:
                failures.append("AGENTS.md missing minimal design-gate routing")
            if "standards/OPERATIONS.md" not in agents_text:
                failures.append("AGENTS.md missing incident/operations routing")
        if checker_managed_profile:
            if mode == "adopted" and version != checker_version:
                failures.append(
                    "engineering_system.version does not match canonical checker version"
                )
            packet_template = root / ".github/ISSUE_TEMPLATE/ai-work-packet.md"
            if packet_template.is_file():
                packet_text = packet_template.read_text(encoding="utf-8", errors="replace")
                for key in ("INTENT_REVISION", "CHANGE_RISK", "EXECUTION_PROFILE", "EXECUTION_PROFILE_REVISION"):
                    if re.search(rf"(?m)^{key}=", packet_text) is None:
                        failures.append(
                            f"managed Work Packet template missing required packet-v3 metadata {key}"
                        )
            for rel in (
                "tools/implementation_preflight.py",
                "tools/terminal_completion_notify.py",
                "tools/execution_profile.py",
                ".engineering/execution-profile.yaml",
                "schemas/execution-profile.schema.json",
                "tools/context_epoch.py",
                "tools/engineering-context.py",
                "tools/governance_floor.py",
                "tools/work_packet_authority.py",
                ".github/ISSUE_TEMPLATE/ai-work-packet.md",
                ".engineering/requirements-engineering-system.txt",
            ):
                target = root / rel
                canonical = Path(__file__).resolve().parents[1] / rel
                if not target.is_file():
                    failures.append(f"managed-profile adoption missing required helper {rel}")
                elif not canonical.is_file():
                    failures.append(f"canonical adoption checker is missing {rel}")
                elif target.read_bytes() != canonical.read_bytes():
                    failures.append(f"{rel} differs from canonical managed helper")
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
        if "tools/skills-contract.py" in agents_text:
            for rel in (
                "tools/skills-contract.py",
                "tools/work_packet_authority.py",
                "schemas/skills-contract.schema.json",
            ):
                if not (root / rel).is_file():
                    failures.append(
                        f"AGENTS.md references skills contract but missing {rel}"
                    )
        if "tools/verification-contract.py" in agents_text:
            for rel in (
                "tools/verification-contract.py",
                "tools/independent_verifier.py",
                "schemas/verification-contract.schema.json",
                "schemas/trust-evidence-receipt.schema.json",
                "schemas/trust-evidence-boundary.schema.json",
            ):
                if not (root / rel).is_file():
                    failures.append(
                        f"AGENTS.md references verification contract but missing {rel}"
                    )
    for rel in retired_agent_artifact_paths():
        path = root / rel
        if path.exists() or path.is_symlink():
            failures.append(f"retired runtime artifact must be removed: {rel}")

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

    if project_path.is_file() and tests_path.is_file():
        try:
            for finding in ci_policy_audit.audit(root):
                failures.append(f"CI policy: {finding}")
        except SystemExit as exc:
            failures.append(f"CI policy audit failed: {exc}")
        except Exception as exc:
            failures.append(f"CI policy audit failed: {exc}")

    release_path = root / ".engineering/release.yaml"
    release: dict = {}
    if release_path.is_file():
        try:
            release = load_yaml(release_path) or {}
            execution_context = release.get("execution_context", "github-hosted")
            if execution_context not in {"github-hosted", "protected-production"}:
                failures.append("release.yaml execution_context is unsupported")
            if bool(release.get("preflight_required")) and not str(release.get("preflight_command") or "").strip():
                failures.append("release.yaml preflight_required=true but preflight_command is empty")
            if version_at_least(version, (1, 5, 0)) and bool(operations.get("production_oriented")):
                if not bool(release.get("operational_e2e_required")):
                    failures.append("production-oriented adoption requires operational_e2e_required=true")
                if int(release.get("full_e2e_passes") or 0) < 1:
                    failures.append("production-oriented adoption requires full_e2e_passes>=1")
                if not bool(release.get("public_smoke_required")):
                    failures.append("production-oriented adoption requires public_smoke_required=true")

            user_facing = bool(project_meta.get("user_facing"))
            primary_user_surface = str(project_meta.get("primary_user_surface") or "none")
            if user_facing:
                if release.get("human_equivalent_user_tests_required") is not True:
                    failures.append("user-facing project requires human_equivalent_user_tests_required=true")
                user_tests = release.get("human_equivalent_user_tests")
                if not isinstance(user_tests, dict):
                    failures.append("user-facing project requires release.human_equivalent_user_tests mapping")
                else:
                    if user_tests.get("contract_version") != 2:
                        failures.append("human-equivalent user tests require contract_version=2")
                    executor = str(user_tests.get("executor") or "").strip()
                    if not executor:
                        failures.append("human-equivalent user tests require an executor")
                    elif checker_managed_profile and executor != "EXECUTION_PROFILE":
                        failures.append(
                            "human-equivalent user tests executor must be EXECUTION_PROFILE"
                        )
                    if user_tests.get("direct_persona_execution_required") is not True:
                        failures.append("human-equivalent user tests require direct_persona_execution_required=true")
                    if user_tests.get("canonical_contract_read_before_execution_required") is not True:
                        failures.append("human-equivalent user tests require canonical_contract_read_before_execution_required=true")
                    if user_tests.get("complete_rerun_after_remediation_required") is not True:
                        failures.append("human-equivalent user tests require complete_rerun_after_remediation_required=true")
                    if user_tests.get("wrapper_user_substitution_forbidden") is not True:
                        failures.append("human-equivalent user tests require wrapper_user_substitution_forbidden=true")
                    attestation = user_tests.get("contract_review_attestation_version")
                    if (
                        isinstance(attestation, bool)
                        or not isinstance(attestation, int)
                        or attestation != 1
                    ):
                        failures.append("human-equivalent user tests require contract_review_attestation_version=1 as an integer")
                    if user_tests.get("actual_user_surface_required") is not True:
                        failures.append("human-equivalent user tests require actual_user_surface_required=true")
                    if str(user_tests.get("primary_user_surface") or "") != primary_user_surface:
                        failures.append("release human-equivalent primary_user_surface must match project.primary_user_surface")
                    if user_tests.get("same_candidate_required") is not True:
                        failures.append("human-equivalent user tests require same_candidate_required=true")
                    if user_tests.get("finding_accumulation_before_remediation") is not True:
                        failures.append("human-equivalent user tests require finding_accumulation_before_remediation=true")
                    if user_tests.get("same_head_quality_closure_required") is not True:
                        failures.append("human-equivalent user tests require same_head_quality_closure_required=true")
                    if user_tests.get("candidate_freeze_after_quality_closure") is not True:
                        failures.append("human-equivalent user tests require candidate_freeze_after_quality_closure=true")
                    if user_tests.get("ci_contract_validation_only") is not True:
                        failures.append("human-equivalent user tests require ci_contract_validation_only=true")
                    if str(user_tests.get("evidence_validator") or "") != "tools/user_acceptance_contract.py":
                        failures.append("human-equivalent user tests require managed user acceptance evidence validator")
                    if primary_user_surface in {"browser", "mixed"} and user_tests.get("actual_browser_process_required") is not True:
                        failures.append("browser user-facing project requires actual_browser_process_required=true")
                    for gate_name in ("surface_reconciliation", "full_user_e2e"):
                        gate = user_tests.get(gate_name)
                        if not isinstance(gate, dict):
                            failures.append(f"human-equivalent user tests missing {gate_name} gate")
                            continue
                        if gate.get("mandatory") is not True:
                            failures.append(f"human-equivalent {gate_name} gate must be mandatory")
                        if int(gate.get("minimum_passes") or 0) < 1:
                            failures.append(f"human-equivalent {gate_name} gate requires minimum_passes>=1")
                        contract_value = gate.get("contract")
                        contract = str(contract_value or "").strip()
                        if not contract:
                            failures.append(f"human-equivalent {gate_name} gate requires contract path")
                        else:
                            contract_status, _ = repository_relative_contract(root, contract_value)
                            if contract_status == "invalid":
                                failures.append(
                                    f"human-equivalent {gate_name} contract must be repository-relative and stay inside repository: {contract}"
                                )
                            elif contract_status == "missing":
                                failures.append(f"human-equivalent {gate_name} contract missing: {contract}")

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
            if f"governance-floor.yml@{baseline}" not in workflow_text:
                failures.append("engineering-system.yml governance floor is not pinned to project baseline")
            if "pull_request_target" not in workflow_text:
                failures.append("engineering-system.yml missing base-branch governance floor trigger")
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
