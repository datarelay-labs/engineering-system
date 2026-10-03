#!/usr/bin/env python3
import json
import re
import subprocess
import tempfile
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_STANDARDS = (
    "standards/CORE.md",
    "standards/DEVELOPMENT.md",
    "standards/DESIGN.md",
    "standards/QUALITY.md",
    "standards/TESTING.md",
    "standards/SECURITY.md",
    "standards/RELEASE.md",
    "standards/USER_ACCEPTANCE.md",
    "standards/OPERATIONS.md",
    "standards/KNOWLEDGE.md",
    "standards/SKILLS.md",
    "standards/ENFORCEMENT.md",
    "standards/SESSION_CONTINUITY.md",
    "standards/PROVIDER_GUIDANCE.md",
    "standards/ADOPTION.md",
)

REQUIRED_ENFORCEMENT_TEMPLATES = (
    "templates/AGENTS.md",
    "templates/CHATGPT_PROJECT_INSTRUCTION.txt",
    "templates/CHATGPT_CUSTOM_INSTRUCTION.txt",
    "templates/.github/ISSUE_TEMPLATE/ai-work-packet.md",
    "templates/.github/workflows/engineering-system.yml",
)

REQUIRED_METHOD_FILES = (
    ".engineering/governance-migration.yaml",
    "adapters/README.md",
    ".github/workflows/affected-tests.yml",
    ".github/workflows/enforcement-check.yml",
    ".github/workflows/governance-floor.yml",
    ".github/workflows/release-preflight.yml",
    ".github/workflows/release-gate.yml",
    ".github/workflows/release-contract.yml",
    "tools/adopt.py",
    "tools/check-adoption.py",
    "tools/upgrade-adoption.py",
    "tools/org-rollout.py",
    "tools/work_packet_authority.py",
    "tools/execution_profile.py",
    "tools/test_execution_profile.py",
    "schemas/execution-profile.schema.json",
    "tools/context_epoch.py",
    "tools/test_context_epoch.py",
    "tools/context_compiler.py",
    "tools/test_context_compiler.py",
    "tools/context_optimization_benchmark.py",
    "tools/test_context_optimization_benchmark.py",
    "tools/context_canary_gate.py",
    "tools/test_context_canary_gate.py",
    "schemas/context-canary-comparison.schema.json",
    "tools/context_shadow_gate.py",
    "tools/test_context_shadow_gate.py",
    "schemas/context-shadow-comparison.schema.json",
    "tools/context_economics.py",
    "tools/test_context_economics.py",
    "schemas/context-economics-report.schema.json",
    "tools/context_fold.py",
    "tools/test_context_fold.py",
    "tools/context_tool_output.py",
    "tools/test_context_tool_output.py",
    "tools/engineering-context.py",
    "tools/test_engineering_context.py",
    "tools/governance_floor.py",
    "tools/test_governance_floor.py",
    "tools/engineering-test.py",
    "tools/test_token_efficiency.py",
    "tools/test_adopt.py",
    "tools/test_org_rollout.py",
    "tools/test_work_packet_authority.py",
    "tools/implementation_preflight.py",
    "tools/test_implementation_preflight.py",
    "tools/work_admission.py",
    "tools/test_work_admission.py",
    "tools/independent_verifier.py",
    "tools/test_independent_verifier.py",
    "tools/coordinator.py",
    "tools/test_coordinator.py",
    "schemas/coordinator-decision.schema.json",
    "tools/coordinator_watch.py",
    "tools/test_coordinator_watch.py",
    "schemas/coordinator-watch.schema.json",
    "tools/coordinator_watch_host.py",
    "tools/test_coordinator_watch_host.py",
    "schemas/coordinator-watch-host.schema.json",
    "tools/terminal_completion_notify.py",
    "tools/test_terminal_completion_notify.py",
    "tools/trusted_worker_adapter.py",
    "tools/test_trusted_worker_adapter.py",
    "tools/worker_adapter.py",
    "tools/test_worker_adapter.py",
    "tools/trusted_external_write_signer.py",
    "tools/test_trusted_external_write_signer.py",
    "tools/trusted_external_write_coordinator.py",
    "tools/test_trusted_external_write_coordinator.py",
    "tools/trusted_boundary_admin.py",
    "tools/test_trusted_boundary_admin.py",
    "tools/trusted_production_write_signer.py",
    "tools/test_trusted_production_write_signer.py",
    "tools/trusted_production_write_coordinator.py",
    "tools/test_trusted_production_write_coordinator.py",
    "tools/test_trusted_production_installed_layout.py",
    "tools/user_acceptance_contract.py",
    "tools/test_user_acceptance_contract.py",
    "schemas/user-acceptance-evidence.schema.json",
    "schemas/worker-adapter-result.schema.json",
    "tools/behavior_eval.py",
    "tools/test_behavior_eval.py",
    "schemas/behavior-scenario.schema.json",
    "schemas/behavior-result.schema.json",
    "evals/behavior/scenarios.yaml",
    "tools/benchmark_fixture.py",
    "tools/test_benchmark_fixture.py",
    "schemas/benchmark-fixture.schema.json",
    "evals/benchmark/fixtures.yaml",
    "tools/benchmark_execution.py",
    "tools/test_benchmark_execution.py",
    "schemas/benchmark-execution.schema.json",
    "tools/efficiency_telemetry.py",
    "tools/test_efficiency_telemetry.py",
    "schemas/efficiency-telemetry.schema.json",
    "tools/knowledge-contract.py",
    "tools/test_knowledge_contract.py",
    "schemas/knowledge-index.schema.json",
    "tools/runtime-contract.py",
    "tools/test_runtime_contract.py",
    "schemas/runtime-contract.schema.json",
    "tools/incident-evidence.py",
    "tools/test_incident_evidence.py",
    "schemas/incident-evidence.schema.json",
    "tools/runtime_evidence.py",
    "tools/test_runtime_evidence.py",
    "tools/skills-contract.py",
    "tools/test_skills_contract.py",
    "schemas/skills-contract.schema.json",
    "tools/verification-contract.py",
    "tools/test_verification_contract.py",
    "schemas/verification-contract.schema.json",
    "schemas/trust-evidence-receipt.schema.json",
    "schemas/trust-evidence-boundary.schema.json",
    "tools/auto_merge_eligibility.py",
    "tools/test_auto_merge_eligibility.py",
    "schemas/auto-merge-eligibility.schema.json",
    "schemas/auto-merge-eligibility-result.schema.json",
    "tools/security-profile.py",
    "tools/test_security_profile.py",
    "schemas/security-profile.schema.json",
    "tools/security-hardening.py",
    "tools/test_security_hardening.py",
    "schemas/security-hardening-plan.schema.json",
)

ACTION_USE_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s@]+)@([^\s#]+)", re.MULTILINE)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def load_json(path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_yaml(path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def validate(instance_path, schema_path):
    instance = load_yaml(ROOT / instance_path)
    schema = load_json(ROOT / schema_path)
    errors = sorted(Draft202012Validator(schema).iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        for error in errors:
            where = ".".join(str(p) for p in error.path) or "<root>"
            print(f"FAIL {instance_path} {where}: {error.message}")
        raise SystemExit(1)
    print(f"PASS {instance_path}")


def require_files(paths):
    missing = [path for path in paths if not (ROOT / path).is_file()]
    if missing:
        for path in missing:
            print(f"FAIL missing required canonical file: {path}")
        raise SystemExit(1)
    for path in paths:
        print(f"PASS required {path}")


def validate_version_alignment():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    self_profile = load_yaml(ROOT / ".engineering/project.yaml")
    template_profile = load_yaml(ROOT / "templates/PROJECT.yaml")
    self_version = (self_profile.get("engineering_system") or {}).get("version")
    template_version = (template_profile.get("engineering_system") or {}).get("version")
    if version != self_version or version != template_version:
        raise SystemExit(
            f"FAIL version mismatch VERSION={version} self={self_version} template={template_version}"
        )
    print(f"PASS engineering-system version alignment {version}")


def validate_baseline_declarations():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    for rel in ("AGENTS.md", "README.md", "templates/AGENTS.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if version not in text:
            raise SystemExit(f"FAIL {rel} missing managed Engineering System version declaration {version}")
        if "baseline" not in text.lower():
            raise SystemExit(f"FAIL {rel} missing managed baseline declaration")
    print(f"PASS managed baseline declarations for {version}")


def validate_bun_discovery():
    adopt_text = (ROOT / "tools/adopt.py").read_text(encoding="utf-8")
    for token in ("bun.lockb", "bun.lock", "bun test", "node_package_manager"):
        if token not in adopt_text:
            raise SystemExit(f"FAIL Bun-native discovery missing token: {token}")
    print("PASS Bun-native test discovery contract")


def validate_work_packet_author_authority():
    session = (ROOT / "standards/SESSION_CONTINUITY.md").read_text(encoding="utf-8")
    authority = (ROOT / "tools/work_packet_authority.py").read_text(encoding="utf-8")
    for token in (
        "WORK_PACKET_AUTHOR_UNTRUSTED",
        "collaborators/{author}/permission",
        "write",
        "maintain",
        "admin",
        "author_association",
        "MUST NOT authorize",
    ):
        if token not in session:
            raise SystemExit(f"FAIL session continuity missing Work Packet author token: {token}")
    for token in (
        "AUTHORIZED_WORK_PACKET_PERMISSIONS",
        "write",
        "maintain",
        "admin",
        "WORK_PACKET_AUTHOR_UNTRUSTED",
        "permission_from_collaborator_payload",
    ):
        if token not in authority:
            raise SystemExit(f"FAIL work packet authority helper missing token: {token}")
    print("PASS trusted Work Packet author authority contract")


def validate_execution_profile_contract():
    profile = ROOT / ".engineering/execution-profile.yaml"
    helper = ROOT / "tools/execution_profile.py"
    tests = ROOT / "tools/test_execution_profile.py"
    schema = ROOT / "schemas/execution-profile.schema.json"
    for path in (profile, helper, tests, schema):
        if not path.is_file():
            raise SystemExit(f"FAIL execution-profile artifact missing: {path.relative_to(ROOT)}")
    payload = yaml.safe_load(profile.read_text(encoding="utf-8")) or {}
    runtime = payload.get("runtime") or {}
    primary = str(runtime.get("primary") or "")
    disabled = {str(item) for item in (runtime.get("disabled") or [])}
    reviewers = {str(item) for item in (runtime.get("optional_reviewers") or [])}
    if not primary or primary in disabled:
        raise SystemExit("FAIL execution profile has invalid primary runtime")
    if payload.get("revision") != 2:
        raise SystemExit("FAIL canonical execution profile revision must be 2")
    if payload.get("authority_contract") != "profile-v3":
        raise SystemExit("FAIL canonical execution profile authority_contract must be profile-v3")
    helper_text = helper.read_text(encoding="utf-8")
    for token in (
        "profile_transition_reasons",
        "packet_authority",
        "retired_rule_present",
        "requires_trusted_boundary",
        "LEGACY_EXECUTION_PROFILE_COMPAT",
    ):
        if token not in helper_text:
            raise SystemExit(f"FAIL execution-profile helper missing invariant: {token}")
    authority_sources = (
        ("AGENTS.md", ROOT / "AGENTS.md"),
        ("templates/AGENTS.md", ROOT / "templates/AGENTS.md"),
        ("templates/CHATGPT_PROJECT_INSTRUCTION.txt", ROOT / "templates/CHATGPT_PROJECT_INSTRUCTION.txt"),
    )
    for rel, path in authority_sources:
        text = path.read_text(encoding="utf-8")
        for token in (
            "current explicit owner instruction",
            "Historical Issue comments",
            "evidence only and never execution authority",
            "execution profile",
        ):
            if token.lower() not in text.lower():
                raise SystemExit(f"FAIL {rel} missing provider-neutral authority invariant: {token}")
    for rel in (
        "AGENTS.md",
        "templates/AGENTS.md",
        "standards/CORE.md",
        "standards/SESSION_CONTINUITY.md",
        "standards/PROVIDER_GUIDANCE.md",
        "tools/context_epoch.py",
        "tools/governance_floor.py",
        "tools/check-adoption.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for runtime_name in {primary, *disabled, *reviewers}:
            if runtime_name and runtime_name in text:
                raise SystemExit(f"FAIL {rel} hard-codes execution runtime {runtime_name}")
    print("PASS provider-neutral execution-profile authority contract")

def validate_work_admission_contract():
    tool = (ROOT / "tools/work_admission.py").read_text(encoding="utf-8")
    for token in ("admit", "ALLOW", "DENY", "SHARED_WORKTREE", "OVERLAPPING_PATHS", "SHARED_RUNTIME"):
        if token not in tool:
            raise SystemExit(f"FAIL work admission tool missing executable invariant: {token}")
    for forbidden in ("os.kill", "persist stop", "SIGKILL"):
        if forbidden in tool:
            raise SystemExit(f"FAIL work admission encodes session mutation: {forbidden}")
    print("PASS optional work-admission executable safety contract")

def validate_independent_verifier_contract():
    tool = (ROOT / "tools/independent_verifier.py").read_text(encoding="utf-8")
    session = (ROOT / "standards/SESSION_CONTINUITY.md").read_text(encoding="utf-8")
    quality = (ROOT / "standards/QUALITY.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    agents_template = (ROOT / "templates/AGENTS.md").read_text(encoding="utf-8")
    for token in (
        "verify",
        "PASS",
        "DENY",
        "SAME_ACTOR",
        "VERIFIER_REQUIRED",
        "HEAD_MISMATCH",
        "ORACLE_NOT_PASS",
        "REVIEW_OPEN",
        "MUTABLE_MISSING",
        "STALE_MUTABLE",
        "HUMAN_APPROVAL_MISSING",
        "EXECUTION_FORBIDDEN",
        "EXECUTES_REQUEST_COMMANDS",
        "subject_head",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL independent verifier missing token: {token}")
    for forbidden in ("import subprocess", "subprocess.", "os.system", "shell=True"):
        if forbidden in tool:
            raise SystemExit(f"FAIL independent verifier encodes command execution: {forbidden}")
    for label, text in (
        ("session continuity", session),
        ("QUALITY.md", quality),
        ("AGENTS.md", agents),
        ("templates/AGENTS.md", agents_template),
    ):
        if "independent_verifier.py" not in text:
            raise SystemExit(f"FAIL {label} missing independent verifier command")
    for token in (
        "Optional independent verifier for additional terminal evidence",
        "exact 40-char subject HEAD",
        "SAME_ACTOR",
        "EXECUTION_FORBIDDEN",
        "STALE_MUTABLE",
    ):
        if token not in session:
            raise SystemExit(f"FAIL session continuity missing verifier token: {token}")
    print("PASS independent verifier terminal-evidence contract")


def validate_coordinator_contract():
    tool = (ROOT / "tools/coordinator.py").read_text(encoding="utf-8")
    session = (ROOT / "standards/SESSION_CONTINUITY.md").read_text(encoding="utf-8")
    enforcement = (ROOT / "standards/ENFORCEMENT.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    agents_template = (ROOT / "templates/AGENTS.md").read_text(encoding="utf-8")
    schema = load_json(ROOT / "schemas/coordinator-decision.schema.json")
    decisions = set(schema["properties"]["decision"]["enum"])
    for token in (
        "plan",
        "--facts",
        "ADMIT_IMPLEMENTATION",
        "RESUME_WORKER",
        "STALE_WORKER",
        "AUDIT_DIRTY_TREE",
        "AUTHORIZE_PUBLICATION",
        "WAIT_EXACT_HEAD_CI",
        "AUDIT_REVIEW",
        "MERGE_READY",
        "YIELD_RESOURCE",
        "RECONCILE_AMBIGUOUS",
        "REPLAN_SEMANTIC_FAILURE",
        "NOOP_PAUSED",
        "NOOP_COMPLETE",
        "BLOCK_HUMAN",
        "STOPS_UNRELATED_SESSIONS",
        "EXECUTION_FORBIDDEN",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL coordinator planner missing token: {token}")
    if decisions != {
        "NOOP_COMPLETE",
        "NOOP_PAUSED",
        "BLOCK_HUMAN",
        "WAIT_EXTERNAL",
        "RECONCILE_AMBIGUOUS",
        "ADMIT_IMPLEMENTATION",
        "RESUME_WORKER",
        "AUDIT_DIRTY_TREE",
        "AUTHORIZE_PUBLICATION",
        "WAIT_EXACT_HEAD_CI",
        "AUDIT_REVIEW",
        "MERGE_READY",
        "REPLAN_SEMANTIC_FAILURE",
        "YIELD_RESOURCE",
        "STALE_WORKER",
    }:
        raise SystemExit("FAIL coordinator decision schema drifted from planner decisions")
    for forbidden in ("import subprocess", "subprocess.", "os.system", "os.kill", "shell=True", "SIGKILL"):
        if forbidden in tool:
            raise SystemExit(f"FAIL coordinator planner encodes a side effect: {forbidden}")
    for label, text in (
        ("session continuity", session),
        ("enforcement", enforcement),
        ("AGENTS.md", agents),
        ("templates/AGENTS.md", agents_template),
    ):
        if "coordinator.py" not in text:
            raise SystemExit(f"FAIL {label} missing coordinator planner command")
    for token in (
        "Pure coordinator planner",
        "coordinator.py plan --facts",
        "RECONCILE_AMBIGUOUS",
        "YIELD_RESOURCE",
        "identical WAIT",
        "progress_evidence=true",
    ):
        if token not in session:
            raise SystemExit(f"FAIL session continuity missing coordinator token: {token}")
    print("PASS pure coordinator planner contract")


def validate_coordinator_watch_contract():
    tool = (ROOT / "tools/coordinator_watch.py").read_text(encoding="utf-8")
    session = (ROOT / "standards/SESSION_CONTINUITY.md").read_text(encoding="utf-8")
    enforcement = (ROOT / "standards/ENFORCEMENT.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    agents_template = (ROOT / "templates/AGENTS.md").read_text(encoding="utf-8")
    schema = load_json(ROOT / "schemas/coordinator-watch.schema.json")
    results = set(schema["properties"]["result"]["enum"])
    for token in (
        "evaluate",
        "--facts",
        "--watch-state",
        "NO_CHANGE",
        "RECHECK_LATER",
        "WAKE_COORDINATOR",
        "RESUME_ADMITTED_WORKER",
        "NOTIFY_OWNER",
        "CLOSE_WATCH",
        "BLOCK_RECONCILIATION",
        "STOPS_UNRELATED_SESSIONS",
        "EXECUTION_FORBIDDEN",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL coordinator watch missing token: {token}")
    if results != {
        "NO_CHANGE",
        "RECHECK_LATER",
        "WAKE_COORDINATOR",
        "RESUME_ADMITTED_WORKER",
        "NOTIFY_OWNER",
        "CLOSE_WATCH",
        "BLOCK_RECONCILIATION",
    }:
        raise SystemExit("FAIL coordinator watch schema drifted from evaluator results")
    for forbidden in ("import subprocess", "subprocess.", "os.system", "os.kill", "shell=True", "SIGKILL", "urllib", "socket"):
        if forbidden in tool:
            raise SystemExit(f"FAIL coordinator watch encodes a side effect: {forbidden}")
    for label, text in (
        ("session continuity", session),
        ("enforcement", enforcement),
        ("AGENTS.md", agents),
        ("templates/AGENTS.md", agents_template),
    ):
        if "coordinator_watch.py" not in text:
            raise SystemExit(f"FAIL {label} missing coordinator watch command")
    for token in (
        "Bounded coordinator watch",
        "coordinator_watch.py evaluate",
        "RECHECK_LATER",
        "WAKE_COORDINATOR",
        "RESUME_ADMITTED_WORKER",
        "BLOCK_RECONCILIATION",
        "CLOSE_WATCH",
        "progress_evidence=true",
    ):
        if token not in session:
            raise SystemExit(f"FAIL session continuity missing coordinator watch token: {token}")
    print("PASS bounded coordinator watch contract")


def validate_coordinator_watch_host_contract():
    tool = (ROOT / "tools/coordinator_watch_host.py").read_text(encoding="utf-8")
    session = (ROOT / "standards/SESSION_CONTINUITY.md").read_text(encoding="utf-8")
    enforcement = (ROOT / "standards/ENFORCEMENT.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    agents_template = (ROOT / "templates/AGENTS.md").read_text(encoding="utf-8")
    schema = load_json(ROOT / "schemas/coordinator-watch-host.schema.json")
    results = set(schema["properties"]["result"]["enum"])
    for token in (
        "run-once",
        "--request",
        "LOCK_HELD",
        "WAKE_COORDINATOR",
        "RESUME_ADMITTED_WORKER",
        "NOTIFY_OWNER",
        "work_budget",
        "EXECUTION_FORBIDDEN",
        "INFO",
        "collect_authoritative",
        "send_effect",
        "consume_replay",
        "reserve_dispatch",
        "finalize_dispatch",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL coordinator watch host missing token: {token}")
    effects = (ROOT / "tools/coordinator_watch_effects.py").read_text(encoding="utf-8")
    collector = (ROOT / "tools/coordinator_watch_collect.py").read_text(encoding="utf-8")
    for token in ("api.telegram.org", "send_github_comment", "shell=False"):
        if token not in effects:
            raise SystemExit(f"FAIL coordinator watch effects missing token: {token}")
    if "shell=True" in effects or "shell=True" in collector:
        raise SystemExit("FAIL coordinator watch delivery encodes a caller shell")
    for token in ("issue\", \"view", "collect_authoritative", "/usr/bin/gh"):
        if token not in collector:
            raise SystemExit(f"FAIL coordinator watch collector missing token: {token}")
    if "resolve_trusted_gh" not in effects:
        raise SystemExit("FAIL coordinator watch effects do not use the trusted gh binary")
    if results != {
        "NO_ACTION",
        "DELIVERED",
        "DEDUP",
        "LOCK_HELD",
        "STALE_RECONCILE",
        "RESOURCE_BLOCKED",
        "RECONCILE_AMBIGUOUS",
        "AUTHORITY_DENIED",
    }:
        raise SystemExit("FAIL coordinator watch host schema drifted from host results")
    for forbidden in (
        "import subprocess",
        "subprocess.",
        "os.system",
        "os.kill",
        "shell=True",
        "urllib",
        "socket",
        "time.sleep",
        "while ",
    ):
        if forbidden in tool:
            raise SystemExit(f"FAIL coordinator watch host encodes a side effect: {forbidden}")
    for label, text in (
        ("session continuity", session),
        ("enforcement", enforcement),
        ("AGENTS.md", agents),
        ("templates/AGENTS.md", agents_template),
    ):
        if "coordinator_watch_host.py" not in text:
            raise SystemExit(f"FAIL {label} missing coordinator watch host command")
    for token in (
        "Coordinator watch host",
        "coordinator_watch_host.py run-once",
        "work_budget",
        "NOTIFY_OWNER",
        "INFO",
    ):
        if token not in session:
            raise SystemExit(f"FAIL session continuity missing coordinator watch host token: {token}")
    print("PASS coordinator watch host contract")


def validate_worker_adapter_contract():
    tool = (ROOT / "tools/worker_adapter.py").read_text(encoding="utf-8")
    session = (ROOT / "standards/SESSION_CONTINUITY.md").read_text(encoding="utf-8")
    enforcement = (ROOT / "standards/ENFORCEMENT.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    agents_template = (ROOT / "templates/AGENTS.md").read_text(encoding="utf-8")
    schema = load_json(ROOT / "schemas/worker-adapter-result.schema.json")
    results = set(schema["properties"]["result"]["enum"])
    for token in (
        "evaluate",
        "APPLIED",
        "NO_CHANGE",
        "STALE_WORKER",
        "AUTHORITY_DENIED",
        "RESOURCE_BLOCKED",
        "RECONCILE_AMBIGUOUS",
        "authorize_work_packet_author_permission",
        "external_write",
        "resolve_trust_anchor",
        "concrete mutation",
        "STOPS_UNRELATED_SESSIONS",
        "MINTS_AUTHORITY",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL worker adapter missing token: {token}")
    if results != {
        "APPLIED",
        "NO_CHANGE",
        "STALE_WORKER",
        "AUTHORITY_DENIED",
        "RESOURCE_BLOCKED",
        "RECONCILE_AMBIGUOUS",
        "TRANSIENT_RETRYABLE",
        "FAILED_SEMANTIC",
    }:
        raise SystemExit("FAIL worker adapter result schema drifted")
    for forbidden in ("import subprocess", "subprocess.", "os.system", "os.kill", "shell=True"):
        if forbidden in tool:
            raise SystemExit(f"FAIL worker adapter encodes a side effect: {forbidden}")
    for label, text in (
        ("session continuity", session),
        ("enforcement", enforcement),
        ("AGENTS.md", agents),
        ("templates/AGENTS.md", agents_template),
    ):
        if "worker_adapter.py" not in text:
            raise SystemExit(f"FAIL {label} missing worker adapter command")
    for token in (
        "Trusted worker external-write adapter",
        "worker_adapter.py evaluate --request-json",
        "STALE_WORKER",
        "author_association",
        "concrete mutation",
    ):
        if token not in session:
            raise SystemExit(f"FAIL session continuity missing worker adapter token: {token}")

    project_instruction = (ROOT / "templates/CHATGPT_PROJECT_INSTRUCTION.txt").read_text(encoding="utf-8")
    custom_instruction = (ROOT / "templates/CHATGPT_CUSTOM_INSTRUCTION.txt").read_text(encoding="utf-8")
    provider_guidance = (ROOT / "standards/PROVIDER_GUIDANCE.md").read_text(encoding="utf-8")
    authority_surfaces = {
        "AGENTS.md": agents,
        "templates/AGENTS.md": agents_template,
        "session continuity": session,
        "enforcement": enforcement,
        "project instruction": project_instruction,
        "custom instruction": custom_instruction,
        "provider guidance": provider_guidance,
    }
    forbidden_universal_gates = (
        "Before an external Issue/PR write, run `python3 tools/worker_adapter.py",
        "Immediately before an Issue/PR write or publication effect",
        "Only authenticated connector authority plus trusted local-binding PASS permits mutation",
    )
    for label, text in authority_surfaces.items():
        for forbidden in forbidden_universal_gates:
            if forbidden in text:
                raise SystemExit(f"FAIL {label} retains universal external-write gate: {forbidden}")
    for label, text in (("AGENTS.md", agents), ("templates/AGENTS.md", agents_template)):
        for token in (
            "continue/resume",
            "additional magic phrase",
            "ordinary authenticated GitHub Issue/PR coordination",
            "high-risk external write",
        ):
            if token not in text:
                raise SystemExit(f"FAIL {label} missing profile-bound execution authority token: {token}")
    for label, text in (("session continuity", session), ("enforcement", enforcement)):
        if "Ordinary authenticated" not in text or "high-risk" not in text or "worker_adapter.py" not in text:
            raise SystemExit(f"FAIL {label} does not scope trusted external-write machinery to high-risk effects")
    for label, text in (("project instruction", project_instruction), ("custom instruction", custom_instruction), ("provider guidance", provider_guidance)):
        if (
            "execution profile" not in text.lower()
            or ("magic phrase" not in text.lower() and "directly edit" not in text)
        ):
            raise SystemExit(f"FAIL {label} missing profile-bound no-magic-phrase guard")
    if "every runnable packet v2/v3" not in custom_instruction:
        raise SystemExit("FAIL custom instruction does not synchronize owner intent for packet v3")
    print("PASS trusted worker external-write adapter contract")

def validate_context_fold_contract():
    tool = (ROOT / "tools/context_fold.py").read_text(encoding="utf-8")
    tests = (ROOT / "tools/test_context_fold.py").read_text(encoding="utf-8")
    for token in (
        "hmac.new",
        "PROTECTED_BLOCK_FOLD_FORBIDDEN",
        "STORE_SYMLINK_FORBIDDEN",
        "STORE_LIMIT_EXCEEDED",
        "fcntl.flock",
        "LOCK_NAME",
        "def staged_put(",
        "STORE_ROLLBACK_FAILED",
        '"decision": "BYPASS"',
        "def purge(",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL context fold helper missing contract token: {token}")
    for token in (
        "CONTEXT_FOLD_TESTS=PASS",
        "test_restrictive_umask_restores_required_owner_permissions",
        "test_concurrent_first_use_is_idempotent",
        "test_concurrent_writers_preserve_store_limit",
        "test_staged_put_rolls_back_only_new_unpublished_entry",
        'if __name__ == "__main__":',
        "raise SystemExit(main())",
    ):
        if token not in tests:
            raise SystemExit(f"FAIL context fold test entrypoint missing token: {token}")
    if tests.count("def test_") < 10:
        raise SystemExit("FAIL context fold regression suite is unexpectedly incomplete")
    print("PASS reversible context fold contract")


def validate_context_tool_output_contract():
    tool = (ROOT / "tools/context_tool_output.py").read_text(encoding="utf-8")
    tests = (ROOT / "tools/test_context_tool_output.py").read_text(encoding="utf-8")
    for token in (
        "context_fold.put",
        "PROTECTED_TOOL_OUTPUT_REDUCTION_FORBIDDEN",
        "DIAGNOSTIC_TERMS",
        '"recovery_marker"',
        '"omitted_ranges"',
        "TOOL_OUTPUT_REDUCER=PASS",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL context tool-output helper missing contract token: {token}")
    for token in (
        "CONTEXT_TOOL_OUTPUT_TESTS=PASS",
        "test_relevant_windows_order_budget_and_recovery",
        "test_overlapping_head_tail_windows_use_unique_required_lines",
        "test_bypass_is_exact_and_store_free",
        "test_telemetry_privacy_and_control_injection",
        "test_failed_cli_publication_rolls_back_new_entry_but_keeps_dedup",
        "test_cli_output_is_private_and_source_is_provider_neutral",
        'if __name__ == "__main__":',
    ):
        if token not in tests:
            raise SystemExit(f"FAIL context tool-output test missing contract token: {token}")
    if tests.count("def test_") < 8:
        raise SystemExit("FAIL context tool-output regression suite is unexpectedly incomplete")
    print("PASS reversible structural tool-output reduction contract")


def validate_context_canary_gate_contract():
    tool = (ROOT / "tools/context_canary_gate.py").read_text(encoding="utf-8")
    tests = (ROOT / "tools/test_context_canary_gate.py").read_text(encoding="utf-8")
    schema = load_json(ROOT / "schemas/context-canary-comparison.schema.json")
    if schema.get("additionalProperties") is not False:
        raise SystemExit("FAIL context canary envelope permits freeform top-level fields")
    for token in (
        "benchmark.score_run_set",
        "telemetry.parse_document",
        "PROFILE_SWITCHED",
        "USAGE_INCOMPLETE",
        "TELEMETRY_RUN_ID_DUPLICATE",
        "TELEMETRY_RUN_ID_REQUIRED",
        "TELEMETRY_RUN_ID_MISMATCH",
        "telemetry.rework_count",
        "SYSTEM_HEAD_MISMATCH",
        "MODEL_COST_MISMATCH",
        "VERIFIED_OUTCOME_INCOMPLETE",
        '"decision": "ELIGIBLE"',
    ):
        if token not in tool:
            raise SystemExit(f"FAIL context canary helper missing contract token: {token}")
    for token in (
        "CONTEXT_CANARY_GATE_TESTS=PASS",
        "test_valid_live_canary_is_eligible_and_factual",
        "test_authoritative_telemetry_identity_fails_closed",
        "test_profile_and_switch_mismatches_fail_closed",
        "test_binding_completeness_and_uniqueness_fail_closed",
        "test_multi_arm_and_head_usage_mismatches_fail_closed",
        "test_telemetry_privacy_contract_cannot_be_bypassed",
        'if __name__ == "__main__":',
    ):
        if token not in tests:
            raise SystemExit(f"FAIL context canary test missing contract token: {token}")
    if tests.count("def test_") < 9:
        raise SystemExit("FAIL context canary regression suite is unexpectedly incomplete")
    print("PASS live context canary comparability contract")


def validate_context_shadow_gate_contract():
    tool = (ROOT / "tools/context_shadow_gate.py").read_text(encoding="utf-8")
    tests = (ROOT / "tools/test_context_shadow_gate.py").read_text(encoding="utf-8")
    schema = load_json(ROOT / "schemas/context-shadow-comparison.schema.json")
    if schema.get("additionalProperties") is not False:
        raise SystemExit("FAIL context shadow envelope permits freeform top-level fields")
    observations = schema.get("properties", {}).get("observations", {})
    if observations.get("maxItems") != 256:
        raise SystemExit("FAIL context shadow observation count is not bounded")
    observation = observations.get("items", {})
    if observation.get("additionalProperties") is not False:
        raise SystemExit("FAIL context shadow observation permits freeform fields")
    actions = observation.get("properties", {}).get("actions", {})
    if actions.get("minItems") != 1 or actions.get("maxItems") != 16:
        raise SystemExit("FAIL context shadow action trace bounds drifted")
    action = actions.get("items", {})
    if action.get("additionalProperties") is not False:
        raise SystemExit("FAIL context shadow action item permits freeform fields")
    for token in (
        "canary_gate.evaluate_comparison",
        "OBSERVATION_TELEMETRY_ID_MISMATCH",
        "OBSERVATION_TELEMETRY_ID_DUPLICATE",
        "ACTION_TRACE_DIVERGED",
        "TERMINAL_COMPLETE_REQUIRED",
        '"decision": "EQUIVALENT"',
    ):
        if token not in tool:
            raise SystemExit(f"FAIL context shadow helper missing contract token: {token}")
    for token in (
        "CONTEXT_SHADOW_GATE_TESTS=PASS",
        "test_identical_material_actions_are_equivalent",
        "test_canary_and_control_fail_closed",
        "test_observation_mapping_fail_closed",
        "test_swapped_observation_identity_is_rejected",
        "test_action_trace_structure_fail_closed",
        "test_block_and_wait_actions_are_compared_not_rejected",
        "test_content_bearing_fields_are_rejected",
        "test_material_action_or_target_divergence_fails_closed",
        'if __name__ == "__main__":',
    ):
        if token not in tests:
            raise SystemExit(f"FAIL context shadow test missing contract token: {token}")
    if tests.count("def test_") < 11:
        raise SystemExit("FAIL context shadow regression suite is unexpectedly incomplete")
    print("PASS shadow context action-equivalence contract")



def validate_context_economics_contract():
    tool = (ROOT / "tools/context_economics.py").read_text(encoding="utf-8")
    tests = (ROOT / "tools/test_context_economics.py").read_text(encoding="utf-8")
    schema = load_json(ROOT / "schemas/context-economics-report.schema.json")
    if schema.get("additionalProperties") is not False:
        raise SystemExit("FAIL context economics report permits freeform top-level fields")
    candidates = schema.get("properties", {}).get("candidates", {})
    if candidates.get("minItems") != 1 or candidates.get("maxItems") != 255:
        raise SystemExit("FAIL context economics candidate bounds drifted")
    candidate = schema.get("$defs", {}).get("candidate", {})
    if candidate.get("additionalProperties") is not False:
        raise SystemExit("FAIL context economics candidate permits freeform fields")
    for token in (
        "shadow_gate.evaluate_shadow",
        "CONTROL_ARM_MISMATCH",
        "RUN_SET_BINDING_MISMATCH",
        "provider_cost_delta_percent",
        "cache_read_tokens",
        "output_tokens",
        "retries",
        '"authority": "EVIDENCE_ONLY"',
        '"promotion_authority": "NONE"',
    ):
        if token not in tool:
            raise SystemExit(f"FAIL context economics helper missing contract token: {token}")
    for forbidden in ("subprocess", "urllib", "requests", "ollama", "paritok"):
        if forbidden in tool.lower():
            raise SystemExit(f"FAIL context economics helper gained forbidden runtime path: {forbidden}")
    for token in (
        "CONTEXT_ECONOMICS_TESTS=PASS",
        "test_lower_cost_with_compensation_is_reported_factually",
        "test_same_and_higher_cost_are_not_relabelled_as_winners",
        "test_zero_cost_control_keeps_percent_unknown",
        "test_shadow_and_control_bindings_fail_closed",
        "test_envelope_and_schema_are_strict",
        "test_cli_and_source_are_offline_non_authorizing",
        'if __name__ == "__main__":',
    ):
        if token not in tests:
            raise SystemExit(f"FAIL context economics test missing contract token: {token}")
    if tests.count("def test_") < 6:
        raise SystemExit("FAIL context economics regression suite is unexpectedly incomplete")
    print("PASS net context economics factual delta contract")


def validate_token_efficiency_contract():
    schema = load_json(ROOT / "schemas/tests.schema.json")
    scenario_props = schema["properties"]["scenarios"]["items"]["properties"]
    for token in ("cost", "estimated_seconds", "timeout_seconds", "agent_default", "scope"):
        if token not in scenario_props:
            raise SystemExit(f"FAIL test manifest schema missing token-efficiency metadata: {token}")

    context_tool = (ROOT / "tools/engineering-context.py").read_text(encoding="utf-8")
    test_tool = (ROOT / "tools/engineering-test.py").read_text(encoding="utf-8")
    for token in ("CHANGED_FILE", "AFFECTED_DOMAINS", "ORIENTATION_HEAD", "ORIENTATION_FILE_JSON", "SLICE_HEAD", "SLICE_LINE_JSON", "mandatory context cannot be sliced", "orientation requires a clean worktree", "CONTEXT_ROUTER=PASS"):
        if token not in context_tool:
            raise SystemExit(f"FAIL engineering-context helper missing token: {token}")
    for token in ("TEST_COST", "SKIP_EXPENSIVE_METADATA_ONLY", "agent-logs"):
        if token not in test_tool:
            raise SystemExit(f"FAIL engineering-test helper missing token: {token}")
    print("PASS token-efficient provider-neutral context/test routing contract")


def validate_issue_template_parity():
    canonical = (ROOT / ".github/ISSUE_TEMPLATE/ai-work-packet.md").read_text(encoding="utf-8")
    template = (ROOT / "templates/.github/ISSUE_TEMPLATE/ai-work-packet.md").read_text(encoding="utf-8")
    if canonical != template:
        raise SystemExit(
            "FAIL templates/.github/ISSUE_TEMPLATE/ai-work-packet.md drifted from canonical issue template"
        )
    print("PASS canonical/template AI Work Packet parity")


def validate_session_continuity_templates():
    issue_text = (ROOT / "templates/.github/ISSUE_TEMPLATE/ai-work-packet.md").read_text(encoding="utf-8")
    issue_tokens = (
        "PACKET_VERSION=3", "TARGET_REPO=", "WORKSTREAM=", "STATUS=ACTIVE",
        "BRANCH=", "TASK_KIND=", "OWNER_INTENT=", "LAST_VERIFIED_HEAD=",
        "EXECUTION_PROFILE=", "EXECUTION_PROFILE_REVISION=", "## Next Action", "## Canonical References",
        "## Latest Evidence", "## Blockers",
    )
    for token in issue_tokens:
        if token not in issue_text:
            raise SystemExit(f"FAIL AI Work Packet template missing token: {token}")
    profile = load_yaml(ROOT / ".engineering/execution-profile.yaml")
    expected_profile = str(profile.get("profile_id") or "")
    expected_revision = str(profile.get("revision") or "")
    for token in (
        f"EXECUTION_PROFILE={expected_profile}",
        f"EXECUTION_PROFILE_REVISION={expected_revision}",
    ):
        if token not in issue_text:
            raise SystemExit(
                f"FAIL AI Work Packet template does not match current execution profile: {token}"
            )
    if "STATUS=DONE" in issue_text:
        raise SystemExit("FAIL AI Work Packet template contains a non-canonical status")
    session = (ROOT / "standards/SESSION_CONTINUITY.md").read_text(encoding="utf-8")
    for token in (
        "PACKET_VERSION=3",
        "Legacy v2 packets remain bounded compatibility inputs",
        "Packet version 2 is legacy-compatible only through the current execution profile.",
        "Packet v1 and versionless packets are non-runnable",
        "8. For packet v3",
        "migrate it to v3",
    ):
        if token not in session:
            raise SystemExit(f"FAIL session continuity missing packet-v3 normative token: {token}")
    for forbidden in (
        "PACKET_VERSION=2\nTARGET_REPO=owner/repository",
        "migrate it to v2",
        "8. For packet v2, require",
        "implementing Chat context also owns terminal audit",
        "Chat may implement directly through the authorized",
    ):
        if forbidden in session:
            raise SystemExit(f"FAIL session continuity retains legacy packet authoring procedure: {forbidden}")
    for token in (
        "WORK_PACKET_SCOPE_MISMATCH", "WORK_PACKET_PROVENANCE_UNTRUSTED",
        "WORK_PACKET_AUTHOR_UNTRUSTED", "MUST NOT authorize", "actionable review",
    ):
        if token not in session:
            raise SystemExit(f"FAIL session continuity missing contract token: {token}")
    print("PASS AI Work Packet and provider-neutral session continuity contract")

def validate_actionable_review_gate():
    required_paths = (
        "standards/CORE.md",
        "AGENTS.md",
        "templates/AGENTS.md",
        "templates/CHATGPT_PROJECT_INSTRUCTION.txt",
        "templates/CHATGPT_CUSTOM_INSTRUCTION.txt",
    )
    missing = []
    for rel in required_paths:
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        if "actionable review" not in text:
            missing.append(rel)
    if missing:
        raise SystemExit("FAIL actionable PR review gate missing from: " + ", ".join(missing))
    print("PASS actionable PR review feedback gate")


def validate_adoption_contract():
    adoption_text = (ROOT / "standards/ADOPTION.md").read_text(encoding="utf-8")
    adopt_text = (ROOT / "tools/adopt.py").read_text(encoding="utf-8")
    required_standard_tokens = (
        "KEEP_STRICTER",
        "DUPLICATE",
        "CONFLICT",
        "ENGINEERING_SYSTEM_ADOPTION=PASS",
        "tools/adopt.py",
        "immutable baseline",
        "Link-only bootstrap",
        "--allow-no-tests",
        "New or empty projects",
    )
    required_tool_tokens = (
        "--audit",
        "--apply",
        "--ack-rule-review",
        "--ci-mode",
        "--operations-mode",
        "--merge-gate-status",
        "--domain-test",
        "QUALITY_CANDIDATES",
        "DOMAIN_CANDIDATES",
        "OPERATIONS_SIGNALS",
        "setup_command",
        "engineering-system.yml",
        "ADOPTION_BOOTSTRAP=PASS",
        "enforcement-check.yml",
        "release-contract.yml",
    )
    failures = []
    for token in required_standard_tokens:
        if token not in adoption_text:
            failures.append(f"adoption standard missing token: {token}")
    for token in required_tool_tokens:
        if token not in adopt_text:
            failures.append(f"adoption tool missing token: {token}")
    if failures:
        for item in failures:
            print(f"FAIL {item}")
        raise SystemExit(1)
    upgrade_text = (ROOT / "tools/upgrade-adoption.py").read_text(encoding="utf-8")
    for token in ("ADOPTION_UPGRADE=PASS", "--baseline-sha", "engineering_system", "release_workflow", "BASELINE_DECLARATIONS_SYNCED"):
        if token not in upgrade_text:
            failures.append(f"adoption upgrade tool missing token: {token}")
    if "rewrite_known_baseline_declarations" not in upgrade_text:
        failures.append("adoption upgrade tool missing baseline declaration sync helper")
    if "plan_baseline_declaration_updates" not in upgrade_text:
        failures.append("adoption upgrade tool missing pre-mutation declaration planner")
    if failures:
        for item in failures:
            print(f"FAIL {item}")
        raise SystemExit(1)
    org_text = (ROOT / "tools/org-rollout.py").read_text(encoding="utf-8")
    adoption_standard = (ROOT / "standards/ADOPTION.md").read_text(encoding="utf-8")
    for token in ("--override-manifest", "flatten_paginated_payload", "--slurp", "OVERRIDE_MANIFEST"):
        if token not in org_text:
            failures.append(f"org rollout tool missing token: {token}")
    for token in (
        "org-rollout.py",
        "override manifest",
        "--override-manifest",
        "AGENTS.md",
        "README.md",
        "version and immutable baseline",
    ):
        if token not in adoption_standard:
            failures.append(f"adoption standard missing org rollout token: {token}")
    if failures:
        for item in failures:
            print(f"FAIL {item}")
        raise SystemExit(1)
    print("PASS automated adoption standard/tool contract")


def validate_adoption_workflow_profile_bundle():
    workflow = (ROOT / ".github/workflows/adoption-compliance.yml").read_text(encoding="utf-8")
    checker = (ROOT / "tools/check-adoption.py").read_text(encoding="utf-8")
    for token in (
        "tools/check-adoption.py",
        "tools/adopt.py",
        ".engineering/execution-profile.yaml",
        "tools/execution_profile.py",
        "schemas/execution-profile.schema.json",
        "tools/governance_floor.py",
        '--expected-baseline "$CALLED_WORKFLOW_SHA"',
        "--expected-mode adopted",
    ):
        if token not in workflow:
            raise SystemExit(
                f"FAIL reusable adoption compliance missing shared-policy token: {token}"
            )
    if "python - <<'PY'" in workflow:
        raise SystemExit("FAIL reusable adoption compliance reimplements checker policy inline")
    for token in (
        "--expected-baseline",
        "--expected-mode",
        "EXECUTION_PROFILE_MANAGED",
        "EXECUTION_PROFILE_REVISION",
        "managed Work Packet template missing required packet-v3 metadata",
        "tools/governance_floor.py",
        "human-equivalent user tests executor must be EXECUTION_PROFILE",
    ):
        if token not in checker:
            raise SystemExit(f"FAIL adoption checker missing shared-policy invariant: {token}")
    print("PASS reusable adoption shared canonical policy contract")


def validate_governance_floor_contract():
    workflow = (ROOT / ".github/workflows/governance-floor.yml").read_text(encoding="utf-8")
    helper = (ROOT / "tools/governance_floor.py").read_text(encoding="utf-8")
    profile_helper = (ROOT / "tools/execution_profile.py").read_text(encoding="utf-8")
    adopted = (ROOT / "templates/.github/workflows/engineering-system.yml").read_text(encoding="utf-8")
    adopt = (ROOT / "tools/adopt.py").read_text(encoding="utf-8")
    check = (ROOT / "tools/check-adoption.py").read_text(encoding="utf-8")
    schema = load_json(ROOT / "schemas/project.schema.json")
    for token in (
        "GOVERNANCE_POLICY_EPOCH_REGRESSION",
        "RETIRED_RUNTIME_REINTRODUCED",
        "RETIRED_RUNTIME_ARTIFACT_REINTRODUCED",
        "GOVERNANCE_SURFACE_CHANGED_WITHOUT_POLICY_EPOCH",
        "GOVERNANCE_ROOT_MIGRATION_MANIFEST_MISSING",
        "ROOT_MIGRATION_MANIFEST",
        "requires_exact_head_validate",
        "automation_eligible",
        "GOVERNANCE_FLOOR=",
        "base_epoch < 3",
        "_has_durable_stage_a_bridge",
        "_has_durable_profile_v3_snapshot",
        "EXECUTION_PROFILE_STAGE_A_EVIDENCE_MISSING",
    ):
        if token not in helper:
            raise SystemExit(f"FAIL governance floor helper missing token: {token}")
    if "EXECUTION_PROFILE_REVISION_NOT_INCREMENTED" not in profile_helper or "profile_transition_reasons" not in helper:
        raise SystemExit("FAIL governance/profile helpers missing execution-profile transition guard")
    for token in ("tools/governance_floor.py check", "--base-ref", "--head-ref"):
        if token not in workflow:
            raise SystemExit(f"FAIL governance floor workflow missing helper invocation token: {token}")
    for token in ("pull_request_target", "governance-floor.yml@", "base_sha:", "head_sha:"):
        if token not in adopted:
            raise SystemExit(f"FAIL adopted workflow missing governance-floor token: {token}")
    canonical_project = load_yaml(ROOT / ".engineering/project.yaml")
    expected_epoch = int((canonical_project.get("engineering_system") or {}).get("policy_epoch") or 0)
    if f"POLICY_EPOCH = {expected_epoch}" not in adopt or "policy_epoch" not in check:
        raise SystemExit("FAIL adoption tooling missing current governance-floor policy epoch")
    policy = (
        schema.get("properties", {})
        .get("engineering_system", {})
        .get("properties", {})
        .get("policy_epoch", {})
    )
    if policy.get("type") != "integer" or policy.get("minimum") != 1:
        raise SystemExit("FAIL project schema policy_epoch is not a positive integer")
    print("PASS base-branch governance floor contract")

def validate_knowledge_contract():
    schema = load_json(ROOT / "schemas/knowledge-index.schema.json")
    Draft202012Validator.check_schema(schema)
    index = load_yaml(ROOT / ".engineering/knowledge.yaml")
    errors = sorted(Draft202012Validator(schema).iter_errors(index), key=lambda item: list(item.path))
    if errors:
        for error in errors:
            where = ".".join(str(part) for part in error.path) or "<root>"
            print(f"FAIL .engineering/knowledge.yaml {where}: {error.message}")
        raise SystemExit(1)
    tool = (ROOT / "tools/knowledge-contract.py").read_text(encoding="utf-8")
    for token in ("KNOWLEDGE_INDEX", "FINDINGS_TRUNCATED", "RETRIEVAL", "STALE_GENERATED", "source_sha256"):
        if token not in tool:
            raise SystemExit(f"FAIL knowledge contract tool missing token: {token}")
    standard = (ROOT / "standards/KNOWLEDGE.md").read_text(encoding="utf-8")
    for token in (".engineering/knowledge.yaml", "RETRIEVAL=LOCAL", "source_sha256"):
        if token not in standard:
            raise SystemExit(f"FAIL knowledge standard missing token: {token}")
    for rel in ("AGENTS.md", "templates/AGENTS.md"):
        if "knowledge.yaml" not in (ROOT / rel).read_text(encoding="utf-8"):
            raise SystemExit(f"FAIL {rel} missing optional knowledge index router")
    adopt_text = (ROOT / "tools/adopt.py").read_text(encoding="utf-8")
    upgrade_text = (ROOT / "tools/upgrade-adoption.py").read_text(encoding="utf-8")
    check_text = (ROOT / "tools/check-adoption.py").read_text(encoding="utf-8")
    for rel in ("tools/knowledge-contract.py", "schemas/knowledge-index.schema.json"):
        if rel not in adopt_text or rel not in check_text:
            raise SystemExit(f"FAIL adopted knowledge path missing from bootstrap or compliance: {rel}")
    if "KNOWLEDGE_CONTRACT_MANAGED" not in upgrade_text or "plan_knowledge_contract_install" not in upgrade_text:
        raise SystemExit("FAIL upgrade-adoption.py missing knowledge contract install")
    import subprocess

    completed = subprocess.run(
        ["python3", "tools/knowledge-contract.py", "check"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode or "RESULT=PASS" not in completed.stdout:
        print(completed.stdout)
        raise SystemExit("FAIL knowledge freshness check")
    print("PASS optional knowledge index freshness contract")


def validate_runtime_contract():
    schema = load_json(ROOT / "schemas/runtime-contract.schema.json")
    Draft202012Validator.check_schema(schema)
    contract = load_yaml(ROOT / ".engineering/runtime.yaml")
    errors = sorted(Draft202012Validator(schema).iter_errors(contract), key=lambda item: list(item.path))
    if errors:
        for error in errors:
            where = ".".join(str(part) for part in error.path) or "<root>"
            print(f"FAIL .engineering/runtime.yaml {where}: {error.message}")
        raise SystemExit(1)
    tool = (ROOT / "tools/runtime-contract.py").read_text(encoding="utf-8")
    for token in (
        "RUNTIME_CONTRACT",
        "FINDINGS_TRUNCATED",
        "HEALTH_AUTHORITY",
        "DUPLICATE_AUTHORITY",
        "operations.health_command",
        "release.public_smoke_command",
        "release.operational_e2e_command",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL runtime contract tool missing token: {token}")
    standard = (ROOT / "standards/OPERATIONS.md").read_text(encoding="utf-8")
    for token in (
        ".engineering/runtime.yaml",
        "operations.health_command",
        "release.public_smoke_command",
        "release.operational_e2e_command",
        "RUNTIME_CONTRACT=ABSENT",
    ):
        if token not in standard:
            raise SystemExit(f"FAIL operations standard missing runtime token: {token}")
    for rel in ("AGENTS.md", "templates/AGENTS.md"):
        if "runtime.yaml" not in (ROOT / rel).read_text(encoding="utf-8"):
            raise SystemExit(f"FAIL {rel} missing optional runtime contract router")
    adopt_text = (ROOT / "tools/adopt.py").read_text(encoding="utf-8")
    upgrade_text = (ROOT / "tools/upgrade-adoption.py").read_text(encoding="utf-8")
    check_text = (ROOT / "tools/check-adoption.py").read_text(encoding="utf-8")
    for rel in ("tools/runtime-contract.py", "schemas/runtime-contract.schema.json"):
        if rel not in adopt_text or rel not in check_text:
            raise SystemExit(f"FAIL adopted runtime path missing from bootstrap or compliance: {rel}")
    if "RUNTIME_CONTRACT_MANAGED" not in upgrade_text or "plan_runtime_contract_install" not in upgrade_text:
        raise SystemExit("FAIL upgrade-adoption.py missing runtime contract install")
    import subprocess

    completed = subprocess.run(
        ["python3", "tools/runtime-contract.py", "check"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode or "RESULT=PASS" not in completed.stdout:
        print(completed.stdout)
        raise SystemExit("FAIL runtime contract check")
    print("PASS optional runtime observability contract")


def validate_incident_evidence():
    schema = load_json(ROOT / "schemas/incident-evidence.schema.json")
    Draft202012Validator.check_schema(schema)
    tool = (ROOT / "tools/incident-evidence.py").read_text(encoding="utf-8")
    for token in (
        "check-packet",
        "capture",
        "AUTHORITY",
        "SAFETY_EFFECT",
        "NARROW",
        "ROOT_CAUSE",
        "UNPROVEN",
        "RETENTION_COUNT_BOUND",
        "RETENTION_SIZE_BOUND",
        "INCIDENT_ID_INVALID",
        "engineering-system/incidents",
        "parse_meminfo",
        "LOW_MEM_AVAILABLE",
        "ELEVATED_MEMORY_PRESSURE",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL incident evidence tool missing token: {token}")
    for banned in ("shell=True", "os.system", "os.kill", "persist stop", "urlopen"):
        if banned in tool:
            raise SystemExit(f"FAIL incident evidence tool contains banned token: {banned}")
    standard = (ROOT / "standards/OPERATIONS.md").read_text(encoding="utf-8")
    for token in (
        "tools/incident-evidence.py",
        "AUTHORITY=NONE",
        "SAFETY_FREEZE=ON",
        "SAFETY_EFFECT=NARROW",
        "ROOT_CAUSE=UNPROVEN",
        "engineering-system/incidents",
    ):
        if token not in standard:
            raise SystemExit(f"FAIL operations standard missing incident token: {token}")
    for rel in ("AGENTS.md", "templates/AGENTS.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "tools/incident-evidence.py" not in text or "SAFETY_FREEZE=ON" not in text:
            raise SystemExit(f"FAIL {rel} missing incident evidence router")
    completed = subprocess.run(["python3", "tools/test_incident_evidence.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    print("PASS incident packet and core evidence contract")


def validate_runtime_evidence():
    tool = (ROOT / "tools/runtime_evidence.py").read_text(encoding="utf-8")
    for token in (
        "collect",
        "production.read",
        "BOUNDARY_UNAVAILABLE",
        "STALE_HEAD",
        "COMMAND_HASH_MISMATCH",
        "BLOCKED_SENSITIVE_OUTPUT",
        "MITIGATION_AUTHORITY",
        "shell=False",
        "engineering-system/incidents",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL runtime evidence tool missing token: {token}")
    for banned in ("shell=True", "os.system", "os.kill", "persist stop", "urlopen"):
        if banned in tool:
            raise SystemExit(f"FAIL runtime evidence tool contains banned token: {banned}")
    standard = (ROOT / "standards/OPERATIONS.md").read_text(encoding="utf-8")
    for token in (
        "tools/runtime_evidence.py",
        "production.read",
        "MITIGATION_AUTHORITY=NONE",
    ):
        if token not in standard:
            raise SystemExit(f"FAIL operations standard missing runtime evidence token: {token}")
    for rel in ("AGENTS.md", "templates/AGENTS.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "tools/runtime_evidence.py collect" not in text:
            raise SystemExit(f"FAIL {rel} missing runtime evidence router")
    completed = subprocess.run(["python3", "tools/test_runtime_evidence.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    print("PASS runtime evidence contract")


def validate_skills_contract():
    schema = load_json(ROOT / "schemas/skills-contract.schema.json")
    Draft202012Validator.check_schema(schema)
    tool = (ROOT / "tools/skills-contract.py").read_text(encoding="utf-8")
    for token in (
        "SKILLS_CONTRACT",
        "TrustedSessionBinding",
        "POLICY_DIGEST_MISMATCH",
        "UNTRUSTED_OVERRIDE",
        "PROFILE_BROADEN",
        "EMPTY_CLASSIFICATION",
        "APPROVAL_REQUIRED",
        "DEFAULT_PROFILES",
        "DEFAULT_TOOL_REGISTRY",
        "BOUNDARY_UNAVAILABLE",
        "TRUST_ANCHOR_ENV",
        "IGNORED_CALLER_TRUST_ANCHOR_ENV",
        "HOST_TRUST_ANCHOR_PATH",
        "HOST_REPLAY_STATE_PATH",
        "REQUEST_BINDING_MISMATCH",
        "canonical_request_sha256",
        "consume_dispatch_once",
        "HOOKS_EXECUTABLE",
        "SCRIPTS_GRANT_EXECUTION",
        "PATH_MISSING",
        "authorize",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL skills contract tool missing token: {token}")
    if "add_parser(\"keygen\"" in tool or "add_parser(\"bind\"" in tool or "add_parser(\"dispatch\"" in tool:
        raise SystemExit("FAIL skills contract exposes minting CLI")
    if "hmac." in tool.lower() or "BINDING_SECRET" in tool or "hmac.new" in tool.lower():
        raise SystemExit("FAIL skills contract retains same-user keyed-MAC/bind mint surface")
    if "os.environ.get(TRUST_ANCHOR_ENV" in tool or "os.environ.get(TRUST_ANCHOR" in tool:
        raise SystemExit("FAIL skills contract resolves trust anchor from caller env")
    if "os.environ" in tool or "environ.get" in tool:
        raise SystemExit("FAIL skills contract must not read process environment")
    if "shutil.which" in tool:
        raise SystemExit("FAIL skills contract resolves verifier from caller PATH")
    if 'HOST_OPENSSL_PATH = Path("/usr/bin/openssl")' not in tool or "resolve_openssl_verifier" not in tool:
        raise SystemExit("FAIL skills contract missing fixed openssl verifier")
    if "HOST_TRUST_ANCHOR_PATH" not in tool or "IGNORED_CALLER_TRUST_ANCHOR_ENV" not in tool:
        raise SystemExit("FAIL skills contract missing host-only trust-anchor disposition")
    if "consume_dispatch_once" not in tool:
        raise SystemExit("FAIL skills contract missing atomic dispatch consume")
    if "def replay_boundary_available" in tool:
        raise SystemExit("FAIL skills contract still uses path-only replay_boundary_available")
    standard = (ROOT / "standards/SKILLS.md").read_text(encoding="utf-8")
    for token in (
        ".engineering/skills.yaml",
        "verification-only",
        "BOUNDARY_UNAVAILABLE",
        "ENGINEERING_SKILLS_TRUST_ANCHOR_PUBKEY",
        "/etc/engineering-system/skills-trust-anchor.pub",
        "/usr/bin/openssl",
        "request_sha256",
        "REQUEST_BINDING_MISMATCH",
        "atomic one-time consume",
        "Same-user file-mode/HMAC",
        "trusted adapter/coordinator",
        "Unsupported platform",
        "P1A-BLOCK-001",
        "Progressive disclosure: body, resources, scripts",
        "machine-checkable non-executable metadata",
        "scripts_grant_execution=false",
    ):
        if token not in standard:
            raise SystemExit(f"FAIL skills standard missing token: {token}")
    for rel in ("AGENTS.md", "templates/AGENTS.md"):
        if "skills.yaml" not in (ROOT / rel).read_text(encoding="utf-8"):
            raise SystemExit(f"FAIL {rel} missing optional skills contract router")
    adopt_text = (ROOT / "tools/adopt.py").read_text(encoding="utf-8")
    upgrade_text = (ROOT / "tools/upgrade-adoption.py").read_text(encoding="utf-8")
    check_text = (ROOT / "tools/check-adoption.py").read_text(encoding="utf-8")
    for rel in ("tools/skills-contract.py", "schemas/skills-contract.schema.json"):
        if rel not in adopt_text or rel not in check_text:
            raise SystemExit(f"FAIL adopted skills path missing from bootstrap or compliance: {rel}")
    if "SKILLS_CONTRACT_MANAGED" not in upgrade_text or "plan_skills_contract_install" not in upgrade_text:
        raise SystemExit("FAIL upgrade-adoption.py missing skills contract install")

    completed = subprocess.run(
        ["python3", "tools/skills-contract.py", "check"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode or "RESULT=PASS" not in completed.stdout:
        print(completed.stdout)
        raise SystemExit("FAIL skills contract check")
    print("PASS optional skills and permission contract")


def validate_verification_contract():
    for rel in (
        "schemas/verification-contract.schema.json",
        "schemas/trust-evidence-receipt.schema.json",
        "schemas/trust-evidence-boundary.schema.json",
    ):
        Draft202012Validator.check_schema(load_json(ROOT / rel))
    tool = (ROOT / "tools/verification-contract.py").read_text(encoding="utf-8")
    for token in (
        "VERIFICATION_CONTRACT",
        "EXTERNAL_MUTATION",
        "EXECUTES_COMMANDS",
        "independent_verifier",
        "evaluate(",
        "AUTOMATION_ELIGIBLE",
        "STALE_HEAD",
        "UNKNOWN_EVIDENCE",
        "MISSING_RUNTIME",
        "SELF_REPORT_ONLY",
        "UNTRUSTED_RECEIPT",
        "UNTRUSTED_BOUNDARY",
        "TrustedCoordinatorBoundary",
        "DIGEST_VERIFIED",
        "external_digest",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL verification contract tool missing token: {token}")
    for banned in ("subprocess", "os.system", "urlopen", "shell=True", "probe_failures", "verifier_request"):
        if banned in tool:
            raise SystemExit(f"FAIL verification contract tool contains banned token: {banned}")
    for rel in ("AGENTS.md", "templates/AGENTS.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "tools/verification-contract.py" not in text or "verification.yaml" not in text:
            raise SystemExit(f"FAIL {rel} missing verification contract router")
    adopt_text = (ROOT / "tools/adopt.py").read_text(encoding="utf-8")
    upgrade_text = (ROOT / "tools/upgrade-adoption.py").read_text(encoding="utf-8")
    check_text = (ROOT / "tools/check-adoption.py").read_text(encoding="utf-8")
    for rel in (
        "tools/verification-contract.py",
        "tools/independent_verifier.py",
        "schemas/verification-contract.schema.json",
        "schemas/trust-evidence-receipt.schema.json",
        "schemas/trust-evidence-boundary.schema.json",
    ):
        if rel not in adopt_text or rel not in check_text:
            raise SystemExit(f"FAIL adopted verification path missing from bootstrap or compliance: {rel}")
    start = adopt_text.index("VERIFICATION_CONTRACT_MANAGED = (")
    block = adopt_text[start : adopt_text.index(")", start)]
    if "verification.yaml" in block:
        raise SystemExit("FAIL adoption managed set must not create verification.yaml")
    if "plan_verification_contract_install" not in upgrade_text:
        raise SystemExit("FAIL upgrade-adoption.py missing verification contract install")
    completed = subprocess.run(
        ["python3", "tools/verification-contract.py", "check"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode or "VERIFICATION_CONTRACT=ABSENT" not in completed.stdout or "RESULT=PASS" not in completed.stdout:
        print(completed.stdout)
        raise SystemExit("FAIL verification contract check")
    print("PASS optional verification and trust evidence contract")


def validate_auto_merge_eligibility_contract():
    for rel in (
        "schemas/auto-merge-eligibility.schema.json",
        "schemas/auto-merge-eligibility-result.schema.json",
    ):
        Draft202012Validator.check_schema(load_json(ROOT / rel))
    tool = (ROOT / "tools/auto_merge_eligibility.py").read_text(encoding="utf-8")
    for token in (
        "ELIGIBLE",
        "DENY",
        "BLOCK",
        "TRUST_BELOW_T5",
        "STALE_HEAD",
        "STALE_CI",
        "STALE_REVIEW",
        "AUTO_MERGE_DISABLED",
        "TrustedCoordinatorBoundary",
        "UNTRUSTED_BOUNDARY",
        "AUTOMATION_NOT_ELIGIBLE",
        "module.assess(",
        'verification["receipt"]',
        'verification["manifest"]',
        '"authorizes_merge": False',
        '"external_mutation": False',
        '"executes_commands": False',
        '"performs_network_io": False',
    ):
        if token not in tool:
            raise SystemExit(f"FAIL auto-merge eligibility tool missing token: {token}")
    if 'add_argument("--boundary"' in tool or "args.boundary" in tool:
        raise SystemExit("FAIL auto-merge eligibility CLI accepts a boundary file")
    for banned in (
        "subprocess",
        "os.system",
        "urlopen",
        "requests",
        "socket",
        "shell=True",
        "merge_pull_request",
        "gh api",
    ):
        if banned in tool:
            raise SystemExit(f"FAIL auto-merge eligibility tool contains banned token: {banned}")
    print("PASS pure conditional auto-merge eligibility contract")


def validate_benchmark_fixtures():
    Draft202012Validator.check_schema(load_json(ROOT / "schemas/benchmark-fixture.schema.json"))
    Draft202012Validator.check_schema(load_json(ROOT / "schemas/benchmark-execution.schema.json"))
    completed = subprocess.run(["python3", "tools/benchmark_fixture.py", "validate"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_benchmark_fixture.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_benchmark_execution.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def validate_security_hardening():
    Draft202012Validator.check_schema(load_json(ROOT / "schemas/security-hardening-plan.schema.json"))
    tool = (ROOT / "tools/security-hardening.py").read_text(encoding="utf-8")
    for token in (
        "security-hardening-plan",
        "DRY_RUN",
        "eligible_apply",
        "plan_digest",
        "allowed_controls",
        "APPLYABLE_CONTROLS",
        "STALE_PLAN",
        '"network": "NONE"',
        "tools/security-profile.py",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL security hardening tool missing token: {token}")
    if 'return ("plan", "apply")' not in tool:
        raise SystemExit("FAIL security hardening tool exposes unexpected commands")
    security = (ROOT / "standards/SECURITY.md").read_text(encoding="utf-8")
    for token in (
        "tools/security-hardening.py",
        "desired-state",
        "dry-run",
        "allowed control",
        "NOT_APPLICABLE",
        "DEFERRED",
    ):
        if token not in security:
            raise SystemExit(f"FAIL SECURITY.md missing security hardening token: {token}")
    for rel in ("AGENTS.md", "templates/AGENTS.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "tools/security-hardening.py plan|apply" not in text:
            raise SystemExit(f"FAIL {rel} missing security hardening router")


def validate_security_profile():
    Draft202012Validator.check_schema(load_json(ROOT / "schemas/security-profile.schema.json"))
    tool = (ROOT / "tools/security-profile.py").read_text(encoding="utf-8")
    for token in (
        "NEEDS_INPUT",
        "EQUIVALENT_EXTERNAL",
        "UNAVAILABLE",
        "DEFERRED",
        "classify",
        "audit",
        '"mutation": "NONE"',
        '"network": "NONE"',
        "FULL_SHA_RE",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL security profile tool missing token: {token}")
    for banned in (
        "subprocess",
        "urlopen",
        "urllib",
        "requests",
        "os.system",
        "shell=True",
        "api.github.com",
        "socket",
    ):
        if banned in tool:
            raise SystemExit(f"FAIL security profile tool contains banned token: {banned}")
    if 'return ("classify", "audit")' not in tool:
        raise SystemExit("FAIL security profile tool exposes commands other than classify and audit")
    security = (ROOT / "standards/SECURITY.md").read_text(encoding="utf-8")
    for token in (
        "production-code",
        "development-code",
        "docs-site",
        "empty-preproduct",
        "NEEDS_INPUT",
        "REQUIRED_PASS",
        "REQUIRED_FAIL",
        "RECOMMENDED_PASS",
        "RECOMMENDED_GAP",
        "EQUIVALENT_EXTERNAL",
        "DEFERRED",
        "UNAVAILABLE",
        "NOT_APPLICABLE",
        "UNKNOWN",
        "40-hex",
        "tools/security-profile.py",
        "allow-no-tests",
        "write-all",
        "GITHUB_TOKEN",
        "audited repository",
        "privileged_tools",
        "commondir",
        "symlink",
        "unreadable",
        "engineering-system.yml",
        "scope",
    ):
        if token not in security:
            raise SystemExit(f"FAIL SECURITY.md missing security profile token: {token}")
    adoption = (ROOT / "standards/ADOPTION.md").read_text(encoding="utf-8")
    for token in (
        "tools/security-profile.py",
        "does not change ordinary adoption failure semantics",
        "ENGINEERING_SYSTEM_ADOPTION=PASS",
        "CodeQL",
        "DEFERRED",
    ):
        if token not in adoption:
            raise SystemExit(f"FAIL ADOPTION.md missing security profile token: {token}")
    for rel in ("AGENTS.md", "templates/AGENTS.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "tools/security-profile.py classify" not in text or "does not mutate GitHub settings" not in text:
            raise SystemExit(f"FAIL {rel} missing security profile router")
    if "security-profile" in (ROOT / "tools/check-adoption.py").read_text(encoding="utf-8"):
        raise SystemExit("FAIL security profile must not change adoption compliance")
    if "security-profile" in (ROOT / "tools/adopt.py").read_text(encoding="utf-8"):
        raise SystemExit("FAIL security profile must not become an adoption managed file")
    print("PASS read-only security profile contract")


def validate_action_pins():
    failures = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for match in ACTION_USE_RE.finditer(text):
            action, ref = match.groups()
            if action.startswith("./"):
                continue
            if not FULL_SHA_RE.fullmatch(ref):
                failures.append((path.relative_to(ROOT), action, ref))
    if failures:
        for path, action, ref in failures:
            print(f"FAIL unpinned GitHub Action {path}: {action}@{ref}")
        raise SystemExit(1)
    print("PASS all external GitHub Actions pinned to full commit SHA")


def main():
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        load_yaml(path)
        print(f"PASS yaml {path.relative_to(ROOT)}")

    require_files(REQUIRED_STANDARDS)
    require_files(REQUIRED_ENFORCEMENT_TEMPLATES)
    require_files(REQUIRED_METHOD_FILES)
    validate_version_alignment()
    validate_baseline_declarations()
    validate_bun_discovery()
    validate_work_packet_author_authority()
    validate_execution_profile_contract()
    validate_work_admission_contract()
    validate_independent_verifier_contract()
    validate_coordinator_contract()
    validate_coordinator_watch_contract()
    validate_coordinator_watch_host_contract()
    validate_worker_adapter_contract()
    validate_context_fold_contract()
    validate_context_tool_output_contract()
    validate_context_canary_gate_contract()
    validate_context_shadow_gate_contract()
    validate_context_economics_contract()
    validate_token_efficiency_contract()
    validate_issue_template_parity()
    validate_session_continuity_templates()
    validate_actionable_review_gate()
    validate_adoption_contract()
    validate_adoption_workflow_profile_bundle()
    validate_governance_floor_contract()
    validate_knowledge_contract()
    validate_runtime_contract()
    validate_incident_evidence()
    validate_runtime_evidence()
    validate_skills_contract()
    validate_verification_contract()
    validate_auto_merge_eligibility_contract()
    validate_security_profile()
    validate_security_hardening()
    validate_action_pins()

    validate(".engineering/project.yaml", "schemas/project.schema.json")
    validate(".engineering/execution-profile.yaml", "schemas/execution-profile.schema.json")
    validate(".engineering/tests.yaml", "schemas/tests.schema.json")
    validate(".engineering/release.yaml", "schemas/release.schema.json")
    validate("templates/PROJECT.yaml", "schemas/project.schema.json")
    validate("templates/TESTS.yaml", "schemas/tests.schema.json")
    validate("templates/RELEASE.yaml", "schemas/release.schema.json")

    completed = subprocess.run(["python3", "tools/test_implementation_preflight.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_work_packet_authority.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_execution_profile.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_context_epoch.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_context_compiler.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_context_optimization_benchmark.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_context_canary_gate.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_context_shadow_gate.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_context_economics.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_context_fold.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_context_tool_output.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_work_admission.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_independent_verifier.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_coordinator.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_coordinator_watch.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_coordinator_watch_host.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_terminal_completion_notify.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_trusted_worker_adapter.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_worker_adapter.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_trusted_external_write_signer.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_trusted_external_write_coordinator.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    for boundary_test in (
        "tools/test_trusted_boundary_admin.py",
        "tools/test_trusted_production_write_signer.py",
        "tools/test_trusted_production_write_coordinator.py",
        "tools/test_trusted_production_installed_layout.py",
    ):
        completed = subprocess.run(["python3", boundary_test], cwd=ROOT)
        if completed.returncode:
            raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_skills_contract.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_verification_contract.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_user_acceptance_contract.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_auto_merge_eligibility.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_security_profile.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_engineering_context.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_governance_floor.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_token_efficiency.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_adopt.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_org_rollout.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/behavior_eval.py", "validate-catalog"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_behavior_eval.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    run = subprocess.run(
        ["python3", "tools/behavior_eval.py", "run"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if run.returncode:
        raise SystemExit(run.returncode or 1)
    head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    with tempfile.TemporaryDirectory() as tmp:
        result_path = Path(tmp) / "behavior-eval-run.json"
        result_path.write_text(run.stdout, encoding="utf-8")
        gate = subprocess.run(
            [
                "python3",
                "tools/behavior_eval.py",
                "gate",
                "--result",
                str(result_path),
                "--head",
                head,
                "--baseline-status",
                "PASS",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    if gate.returncode:
        print("FAIL behavior eval rollout gate")
        raise SystemExit(gate.returncode)
    print("PASS behavior eval regression and deterministic gate")
    validate_benchmark_fixtures()

    completed = subprocess.run(["python3", "tools/test_knowledge_contract.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_efficiency_telemetry.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/efficiency_telemetry.py", "gate"], cwd=ROOT)
    if completed.returncode:
        print("FAIL efficiency telemetry privacy and task-budget gate")
        raise SystemExit(completed.returncode)
    print("PASS efficiency telemetry privacy and task-budget gate")

    print("ENGINEERING_SYSTEM_VALIDATION=PASS")


if __name__ == "__main__":
    main()
