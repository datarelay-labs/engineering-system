#!/usr/bin/env python3
import json
import re
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
    "standards/ENFORCEMENT.md",
    "standards/SESSION_CONTINUITY.md",
    "standards/ADOPTION.md",
)

REQUIRED_ENFORCEMENT_TEMPLATES = (
    "templates/AGENTS.md",
    "templates/.cursor/rules/engineering-system.mdc",
    "templates/CHATGPT_PROJECT_INSTRUCTION.txt",
    "templates/CHATGPT_CUSTOM_INSTRUCTION.txt",
    "templates/CURSOR_USER_RULE.txt",
    "templates/.cursor/commands/resume.md",
    "templates/.github/ISSUE_TEMPLATE/ai-work-packet.md",
    "templates/.github/workflows/engineering-system.yml",
)

REQUIRED_METHOD_FILES = (
    "adapters/README.md",
    ".github/workflows/affected-tests.yml",
    ".github/workflows/release-preflight.yml",
    ".github/workflows/release-gate.yml",
    "tools/adopt.py",
    "tools/check-adoption.py",
    "tools/test_adopt.py",
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



def validate_resume_template_parity():
    canonical = (ROOT / ".cursor/commands/resume.md").read_text(encoding="utf-8")
    template = (ROOT / "templates/.cursor/commands/resume.md").read_text(encoding="utf-8")
    if canonical != template:
        raise SystemExit(
            "FAIL templates/.cursor/commands/resume.md drifted from canonical .cursor/commands/resume.md"
        )
    print("PASS canonical/template Cursor resume parity")


def validate_session_continuity_templates():
    issue_path = ROOT / "templates/.github/ISSUE_TEMPLATE/ai-work-packet.md"
    resume_path = ROOT / "templates/.cursor/commands/resume.md"

    issue_text = issue_path.read_text(encoding="utf-8")
    resume_text = resume_path.read_text(encoding="utf-8")

    issue_tokens = (
        "PACKET_VERSION=1",
        "TARGET_REPO=",
        "WORKSTREAM=",
        "STATUS=ACTIVE",
        "BRANCH=",
        "LAST_VERIFIED_HEAD=",
        "## Next Action",
        "## Canonical References",
        "## Latest Evidence",
        "## Blockers",
    )
    resume_tokens = (
        "git remote get-url origin",
        "git branch --show-current",
        "git rev-parse HEAD",
        "TARGET_REPO",
        "STATUS=ACTIVE",
        "BRANCH",
        "exactly one match",
        "Next Action",
        "update the same Work Packet",
    )

    failures = []
    for token in issue_tokens:
        if token not in issue_text:
            failures.append(f"AI Work Packet template missing token: {token}")
    for token in resume_tokens:
        if token not in resume_text:
            failures.append(f"Cursor resume template missing token: {token}")

    if failures:
        for item in failures:
            print(f"FAIL {item}")
        raise SystemExit(1)

    print("PASS AI Work Packet and Cursor resume template contract")


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
    print("PASS automated adoption standard/tool contract")


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
    validate_resume_template_parity()
    validate_session_continuity_templates()
    validate_adoption_contract()
    validate_action_pins()

    validate(".engineering/project.yaml", "schemas/project.schema.json")
    validate(".engineering/tests.yaml", "schemas/tests.schema.json")
    validate(".engineering/release.yaml", "schemas/release.schema.json")
    validate("templates/PROJECT.yaml", "schemas/project.schema.json")
    validate("templates/TESTS.yaml", "schemas/tests.schema.json")
    validate("templates/RELEASE.yaml", "schemas/release.schema.json")

    import subprocess
    completed = subprocess.run(["python3", "tools/test_adopt.py"], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)

    print("ENGINEERING_SYSTEM_VALIDATION=PASS")


if __name__ == "__main__":
    main()
