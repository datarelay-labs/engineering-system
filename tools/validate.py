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
    "standards/OPERATIONS.md",
    "standards/KNOWLEDGE.md",
    "standards/SKILLS.md",
    "standards/ENFORCEMENT.md",
    "standards/SESSION_CONTINUITY.md",
    "standards/ADOPTION.md",
)

REQUIRED_ENFORCEMENT_TEMPLATES = (
    "templates/AGENTS.md",
    "templates/.cursor/rules/engineering-system.mdc",
    "templates/.cursorignore",
    "templates/CHATGPT_PROJECT_INSTRUCTION.txt",
    "templates/CHATGPT_CUSTOM_INSTRUCTION.txt",
    "templates/CURSOR_USER_RULE.txt",
    "templates/.cursor/commands/resume.md",
    "templates/.cursor/commands/work-resume.md",
    "templates/.github/ISSUE_TEMPLATE/ai-work-packet.md",
    "templates/.github/workflows/engineering-system.yml",
)

REQUIRED_METHOD_FILES = (
    "adapters/README.md",
    ".github/workflows/affected-tests.yml",
    ".github/workflows/enforcement-check.yml",
    ".github/workflows/release-preflight.yml",
    ".github/workflows/release-gate.yml",
    ".github/workflows/release-contract.yml",
    "tools/adopt.py",
    "tools/check-adoption.py",
    "tools/upgrade-adoption.py",
    "tools/org-rollout.py",
    "tools/work_packet_authority.py",
    "tools/engineering-context.py",
    "tools/engineering-test.py",
    "tools/test_token_efficiency.py",
    "tools/test_adopt.py",
    "tools/test_org_rollout.py",
    "tools/test_work_packet_authority.py",
    "tools/cursor-resource-preflight.py",
    "tools/test_cursor_resource_preflight.py",
    "tools/work_admission.py",
    "tools/test_work_admission.py",
    "tools/independent_verifier.py",
    "tools/test_independent_verifier.py",
    "tools/behavior_eval.py",
    "tools/test_behavior_eval.py",
    "schemas/behavior-scenario.schema.json",
    "schemas/behavior-result.schema.json",
    "evals/behavior/scenarios.yaml",
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
    "tools/skills-contract.py",
    "tools/test_skills_contract.py",
    "schemas/skills-contract.schema.json",
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
    resume = (ROOT / "templates/.cursor/commands/resume.md").read_text(encoding="utf-8")
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
        if token not in resume:
            raise SystemExit(f"FAIL resume template missing Work Packet author token: {token}")
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


def validate_resource_guard_contract():
    tool = (ROOT / "tools/cursor-resource-preflight.py").read_text(encoding="utf-8")
    session = (ROOT / "standards/SESSION_CONTINUITY.md").read_text(encoding="utf-8")
    instruction = (ROOT / "templates/CHATGPT_CUSTOM_INSTRUCTION.txt").read_text(encoding="utf-8")
    resume = (ROOT / "templates/.cursor/commands/resume.md").read_text(encoding="utf-8")
    for token in ("PASS", "WARN", "BLOCK", "agent persist list", "MemAvailable"):
        if token not in tool:
            raise SystemExit(f"FAIL resource preflight missing token: {token}")
    for token in ("os.kill", "persist stop", "SIGKILL"):
        if token in tool:
            raise SystemExit(f"FAIL resource preflight encodes session mutation: {token}")
    for label, text in (
        ("session continuity", session),
        ("ChatGPT custom instruction", instruction),
        ("resume template", resume),
    ):
        if "cursor-resource-preflight.py" not in text:
            raise SystemExit(f"FAIL {label} missing resource preflight command")
        if "BLOCK" not in text:
            raise SystemExit(f"FAIL {label} missing BLOCK result")
    if instruction.index("tools/cursor-resource-preflight.py") > instruction.index("agent persist /work-resume"):
        raise SystemExit("FAIL ChatGPT handoff runs agent persist before resource preflight")
    print("PASS Cursor persistent-session resource guard contract")


def validate_work_admission_contract():
    tool = (ROOT / "tools/work_admission.py").read_text(encoding="utf-8")
    session = (ROOT / "standards/SESSION_CONTINUITY.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    agents_template = (ROOT / "templates/AGENTS.md").read_text(encoding="utf-8")
    for token in (
        "admit",
        "size",
        "release",
        "ALLOW",
        "DENY",
        "WIP_LIMIT",
        "SHARED_WORKTREE",
        "OVERLAPPING_CLAIM",
        "STALE_INTENT_REVISION",
        "OVERLAPPING_PATHS",
        "SHARED_RUNTIME",
        "HOST_BUDGET",
        "AMBIGUOUS_CLAIM",
        "INSUFFICIENT_OWNERSHIP",
        "BATCH",
        "KEEP",
        "SPLIT",
        "MUTATES_EXISTING_SESSIONS",
    ):
        if token not in tool:
            raise SystemExit(f"FAIL work admission tool missing token: {token}")
    for forbidden in ("os.kill", "persist stop", "SIGKILL"):
        if forbidden in tool:
            raise SystemExit(f"FAIL work admission encodes session mutation: {forbidden}")
    for label, text in (
        ("session continuity", session),
        ("AGENTS.md", agents),
        ("templates/AGENTS.md", agents_template),
    ):
        if "work_admission.py" not in text:
            raise SystemExit(f"FAIL {label} missing work admission command")
    if "obey DENY" not in agents or "obey DENY" not in agents_template:
        raise SystemExit("FAIL AGENTS missing work admission DENY obedience")
    for token in (
        "Parallel-work admission and WIP ownership",
        "work_admission.py admit",
        "work_admission.py size",
        "work_admission.py release",
        "dirty, unpushed, or ambiguous",
    ):
        if token not in session:
            raise SystemExit(f"FAIL session continuity missing admission token: {token}")
    print("PASS parallel-work admission and WIP ownership contract")


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
        "Independent verifier for terminal evidence",
        "exact 40-char subject HEAD",
        "SAME_ACTOR",
        "EXECUTION_FORBIDDEN",
        "STALE_MUTABLE",
    ):
        if token not in session:
            raise SystemExit(f"FAIL session continuity missing verifier token: {token}")
    print("PASS independent verifier terminal-evidence contract")


def validate_token_efficiency_contract():
    rule = (ROOT / ".cursor/rules/engineering-system.mdc").read_text(encoding="utf-8")
    rule_template = (ROOT / "templates/.cursor/rules/engineering-system.mdc").read_text(encoding="utf-8")
    if rule != rule_template:
        raise SystemExit("FAIL canonical/template Cursor engineering-system rule drift")
    if len(rule.splitlines()) > 16:
        raise SystemExit("FAIL always-applied Cursor rule exceeds compact context budget")
    if "@AGENTS.md" in rule or "@.engineering/project.yaml" in rule:
        raise SystemExit("FAIL always-applied Cursor rule force-attaches repository context")
    for token in ("diff", "cheapest", "verbose", "polling"):
        if token not in rule:
            raise SystemExit(f"FAIL compact Cursor rule missing token-efficiency token: {token}")

    ignore = (ROOT / ".cursorignore").read_text(encoding="utf-8")
    ignore_template = (ROOT / "templates/.cursorignore").read_text(encoding="utf-8")
    if ignore != ignore_template:
        raise SystemExit("FAIL canonical/template .cursorignore drift")
    for token in ("node_modules/", "__pycache__/", "*.log"):
        if token not in ignore:
            raise SystemExit(f"FAIL .cursorignore missing safe-noise token: {token}")

    schema = load_json(ROOT / "schemas/tests.schema.json")
    scenario_props = schema["properties"]["scenarios"]["items"]["properties"]
    for token in ("cost", "estimated_seconds", "timeout_seconds", "agent_default", "scope"):
        if token not in scenario_props:
            raise SystemExit(f"FAIL test manifest schema missing token-efficiency metadata: {token}")

    context_tool = (ROOT / "tools/engineering-context.py").read_text(encoding="utf-8")
    test_tool = (ROOT / "tools/engineering-test.py").read_text(encoding="utf-8")
    for token in ("CHANGED_FILE", "AFFECTED_DOMAINS", "CONTEXT_ROUTER=PASS"):
        if token not in context_tool:
            raise SystemExit(f"FAIL engineering-context helper missing token: {token}")
    for token in ("TEST_COST", "SKIP_EXPENSIVE_METADATA_ONLY", "agent-logs"):
        if token not in test_tool:
            raise SystemExit(f"FAIL engineering-test helper missing token: {token}")
    print("PASS token-efficient Cursor context/test routing contract")


def validate_resume_template_parity():
    canonical = (ROOT / ".cursor/commands/resume.md").read_text(encoding="utf-8")
    template = (ROOT / "templates/.cursor/commands/resume.md").read_text(encoding="utf-8")
    if canonical != template:
        raise SystemExit(
            "FAIL templates/.cursor/commands/resume.md drifted from canonical .cursor/commands/resume.md"
        )
    for rel in (
        ".cursor/commands/work-resume.md",
        "templates/.cursor/commands/work-resume.md",
    ):
        alias = (ROOT / rel).read_text(encoding="utf-8")
        if alias != canonical:
            raise SystemExit(f"FAIL {rel} drifted from canonical .cursor/commands/resume.md")
    print("PASS canonical/template Cursor resume parity")


def validate_issue_template_parity():
    canonical = (ROOT / ".github/ISSUE_TEMPLATE/ai-work-packet.md").read_text(encoding="utf-8")
    template = (ROOT / "templates/.github/ISSUE_TEMPLATE/ai-work-packet.md").read_text(encoding="utf-8")
    if canonical != template:
        raise SystemExit(
            "FAIL templates/.github/ISSUE_TEMPLATE/ai-work-packet.md drifted from canonical issue template"
        )
    print("PASS canonical/template AI Work Packet parity")


def validate_session_continuity_templates():
    issue_path = ROOT / "templates/.github/ISSUE_TEMPLATE/ai-work-packet.md"
    resume_path = ROOT / "templates/.cursor/commands/resume.md"

    issue_text = issue_path.read_text(encoding="utf-8")
    resume_text = resume_path.read_text(encoding="utf-8")

    issue_tokens = (
        "PACKET_VERSION=2",
        "TARGET_REPO=",
        "WORKSTREAM=",
        "STATUS=ACTIVE",
        "BRANCH=",
        "TASK_KIND=",
        "OWNER_INTENT=",
        "LAST_VERIFIED_HEAD=",
        "## Next Action",
        "## Canonical References",
        "## Latest Evidence",
        "## Blockers",
    )
    resume_tokens = (
        "ENVIRONMENT_BLOCKER",
        "git remote get-url origin",
        "git branch --show-current",
        "git rev-parse HEAD",
        "TARGET_REPO",
        "STATUS=ACTIVE",
        "TASK_KIND",
        "OWNER_INTENT",
        "WORK_PACKET_SCOPE_MISMATCH",
        "WORK_PACKET_PROVENANCE_UNTRUSTED",
        "WORK_PACKET_AUTHOR_UNTRUSTED",
        "collaborators/{author}/permission",
        "write",
        "maintain",
        "admin",
        "author_association",
        "MUST NOT authorize",
        "authenticated",
        "ENGINEERING_SYSTEM_ADOPTION=INCOMPLETE",
        "TASK_KIND=ADOPTION",
        "exactly one match",
        "Next Action",
        "STATUS=COMPLETE",
        "actionable review feedback",
        "update the same Work Packet",
    )

    failures = []
    for token in issue_tokens:
        if token not in issue_text:
            failures.append(f"AI Work Packet template missing token: {token}")
    for token in resume_tokens:
        if token not in resume_text:
            failures.append(f"Cursor resume template missing token: {token}")

    forbidden_resume = ("STATUS=DONE", "STATUS=CURSOR_READY")
    for token in forbidden_resume:
        if token in resume_text:
            failures.append(f"Cursor resume template contains non-canonical status: {token}")

    if "CURSOR_READY" in issue_text or "STATUS=DONE" in issue_text:
        failures.append("AI Work Packet template contains a non-canonical status")

    if failures:
        for item in failures:
            print(f"FAIL {item}")
        raise SystemExit(1)

    print("PASS AI Work Packet and Cursor resume template contract")


def validate_actionable_review_gate():
    required_paths = (
        "standards/CORE.md",
        "AGENTS.md",
        "templates/AGENTS.md",
        ".cursor/commands/resume.md",
        "templates/.cursor/commands/resume.md",
        ".cursor/rules/engineering-system.mdc",
        "templates/.cursor/rules/engineering-system.mdc",
        "templates/CHATGPT_PROJECT_INSTRUCTION.txt",
        "templates/CHATGPT_CUSTOM_INSTRUCTION.txt",
        "templates/CURSOR_USER_RULE.txt",
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
        "parse_persist_list",
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
    validate_resource_guard_contract()
    validate_work_admission_contract()
    validate_independent_verifier_contract()
    validate_token_efficiency_contract()
    validate_resume_template_parity()
    validate_issue_template_parity()
    validate_session_continuity_templates()
    validate_actionable_review_gate()
    validate_adoption_contract()
    validate_knowledge_contract()
    validate_runtime_contract()
    validate_incident_evidence()
    validate_skills_contract()
    validate_action_pins()

    validate(".engineering/project.yaml", "schemas/project.schema.json")
    validate(".engineering/tests.yaml", "schemas/tests.schema.json")
    validate(".engineering/release.yaml", "schemas/release.schema.json")
    validate("templates/PROJECT.yaml", "schemas/project.schema.json")
    validate("templates/TESTS.yaml", "schemas/tests.schema.json")
    validate("templates/RELEASE.yaml", "schemas/release.schema.json")

    completed = subprocess.run(["python3", "tools/test_cursor_resource_preflight.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_work_packet_authority.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_work_admission.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_independent_verifier.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(["python3", "tools/test_skills_contract.py"], cwd=ROOT)
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
