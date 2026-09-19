#!/usr/bin/env python3
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_STANDARDS = (
    "standards/CORE.md",
    "standards/DEVELOPMENT.md",
    "standards/QUALITY.md",
    "standards/TESTING.md",
    "standards/SECURITY.md",
    "standards/RELEASE.md",
    "standards/OPERATIONS.md",
    "standards/KNOWLEDGE.md",
    "standards/ENFORCEMENT.md",
)

REQUIRED_ENFORCEMENT_TEMPLATES = (
    "templates/AGENTS.md",
    "templates/.cursor/rules/engineering-system.mdc",
    "templates/CHATGPT_PROJECT_INSTRUCTION.txt",
    "templates/CHATGPT_CUSTOM_INSTRUCTION.txt",
    "templates/CURSOR_USER_RULE.txt",
)


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


def main():
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        load_yaml(path)
        print(f"PASS yaml {path.relative_to(ROOT)}")

    require_files(REQUIRED_STANDARDS)
    require_files(REQUIRED_ENFORCEMENT_TEMPLATES)
    validate_version_alignment()

    validate(".engineering/project.yaml", "schemas/project.schema.json")
    validate(".engineering/tests.yaml", "schemas/tests.schema.json")
    validate(".engineering/release.yaml", "schemas/release.schema.json")
    validate("templates/PROJECT.yaml", "schemas/project.schema.json")
    validate("templates/TESTS.yaml", "schemas/tests.schema.json")
    validate("templates/RELEASE.yaml", "schemas/release.schema.json")

    print("ENGINEERING_SYSTEM_VALIDATION=PASS")


if __name__ == "__main__":
    main()
