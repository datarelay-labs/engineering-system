#!/usr/bin/env python3
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


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


def main():
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        load_yaml(path)
        print(f"PASS yaml {path.relative_to(ROOT)}")

    validate(".engineering/project.yaml", "schemas/project.schema.json")
    validate(".engineering/tests.yaml", "schemas/tests.schema.json")
    validate(".engineering/release.yaml", "schemas/release.schema.json")
    validate("templates/PROJECT.yaml", "schemas/project.schema.json")
    validate("templates/TESTS.yaml", "schemas/tests.schema.json")
    validate("templates/RELEASE.yaml", "schemas/release.schema.json")

    print("ENGINEERING_SYSTEM_VALIDATION=PASS")


if __name__ == "__main__":
    main()
