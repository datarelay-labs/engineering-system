#!/usr/bin/env python3
"""Optional runtime/observability contract resolver.

Health, smoke, and operational E2E commands stay in the existing project and
release profiles. This tool does not execute commands, does not call a vendor,
and does not create `.engineering/runtime.yaml`.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

DEFAULT_MAX_FINDINGS = 20
CONTRACT_REL = Path(".engineering") / "runtime.yaml"
SCHEMA_REL = Path("schemas") / "runtime-contract.schema.json"
PROJECT_REL = Path(".engineering") / "project.yaml"
RELEASE_REL = Path(".engineering") / "release.yaml"

AUTHORITY_FIELDS = {
    "health": "operations.health_command",
    "smoke": "release.public_smoke_command",
    "e2e": "release.operational_e2e_command",
}
ADDITIVE = ("start", "logs", "browser", "metrics", "traces", "cleanup")
EVIDENCE = {"logs", "browser", "metrics", "traces"}


def fail_usage(message: str) -> None:
    raise SystemExit(f"RUNTIME_CONTRACT=FAIL {message}")


def finding(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail[:180]}


def load_mapping(path: Path) -> tuple[dict | None, str | None]:
    if not path.is_file():
        return None, "MISSING"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, str(exc).splitlines()[0]
    if not isinstance(payload, dict):
        return None, "mapping required"
    return payload, None


def schema_findings(root: Path, instance: object) -> list[dict[str, str]]:
    schema_path = root / SCHEMA_REL
    if not schema_path.is_file():
        return [finding("SCHEMA", f"missing {SCHEMA_REL.as_posix()}")]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda item: list(item.path),
    )
    return [finding("SCHEMA", error.message.replace("\n", " ")) for error in errors]


def authority_value(kind: str, project: dict | None, release: dict | None) -> tuple[str, str]:
    if kind == "health":
        operations = project.get("operations") if isinstance(project, dict) else None
        raw = operations.get("health_command") if isinstance(operations, dict) else None
    elif kind == "smoke":
        raw = release.get("public_smoke_command") if isinstance(release, dict) else None
    else:
        raw = release.get("operational_e2e_command") if isinstance(release, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return "UNSUPPORTED", ""
    return "SUPPORTED", raw.strip()


def check_contract(root: Path) -> dict[str, object]:
    contract_path = root / CONTRACT_REL
    project, project_problem = load_mapping(root / PROJECT_REL)
    release, release_problem = load_mapping(root / RELEASE_REL)
    present = contract_path.is_file()
    findings: list[dict[str, str]] = []

    def note_profile(problem: str | None, label: str) -> None:
        if problem == "MISSING":
            if present:
                findings.append(finding("MISSING_AUTHORITY", label))
            return
        if problem:
            findings.append(finding("INVALID_AUTHORITY", f"{label} {problem}"))

    note_profile(project_problem, PROJECT_REL.as_posix())
    note_profile(release_problem, RELEASE_REL.as_posix())

    authorities: dict[str, dict[str, str]] = {}
    for kind, field in AUTHORITY_FIELDS.items():
        support, command = authority_value(kind, project, release)
        authorities[kind] = {"field": field, "support": support, "command": command}

    capabilities: dict[str, dict[str, str]] = {}
    if not present:
        for name in ADDITIVE:
            capabilities[name] = {"support": "UNSPECIFIED"}
    else:
        try:
            instance = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            instance = None
            findings.append(finding("SCHEMA", str(exc).splitlines()[0]))
        if instance is not None:
            findings.extend(schema_findings(root, instance))
        if isinstance(instance, dict) and not any(item["code"] == "SCHEMA" for item in findings):
            declared = instance.get("capabilities") or {}
            authority_commands = {
                item["command"] for item in authorities.values() if item["command"]
            }
            for name in ADDITIVE:
                spec = declared.get(name) or {}
                support = spec.get("support")
                entry: dict[str, str] = {
                    "support": "SUPPORTED" if support == "supported" else "UNSUPPORTED"
                }
                if support == "supported":
                    command = str(spec.get("command") or "")
                    entry["command"] = command
                    if command in authority_commands:
                        findings.append(finding("DUPLICATE_AUTHORITY", name))
                    if name in EVIDENCE:
                        fuller = str(spec.get("fuller_command") or "")
                        entry["fuller_command"] = fuller
                        if fuller in authority_commands:
                            findings.append(finding("DUPLICATE_AUTHORITY", f"{name} fuller_command"))
                capabilities[name] = entry
        else:
            for name in ADDITIVE:
                capabilities[name] = {"support": "UNSPECIFIED"}

    ordered = sorted(findings, key=lambda item: (item["code"], item["detail"]))
    return {
        "runtime_contract": "PRESENT" if present else "ABSENT",
        "result": "FAIL" if ordered else "PASS",
        "authorities": authorities,
        "capabilities": capabilities,
        "findings": ordered,
    }


def command_for(root: Path, mode: str) -> str:
    command = f"python3 tools/runtime-contract.py check --{mode}"
    if root.resolve() != Path.cwd().resolve():
        command += " --root " + shlex.quote(str(root))
    return command


def emit_check(root: Path, report: dict[str, object], max_findings: int, mode: str) -> None:
    if mode == "raw":
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return
    findings = list(report["findings"])
    shown = findings if mode == "full" else findings[:max_findings]
    print(f"RUNTIME_CONTRACT={report['runtime_contract']}")
    print(f"RESULT={report['result']}")
    authorities = report["authorities"]
    health = authorities["health"]
    smoke = authorities["smoke"]
    e2e = authorities["e2e"]
    print(f"HEALTH_AUTHORITY={health['field']}")
    print(f"HEALTH_SUPPORT={health['support']}")
    if health["command"]:
        print(f"HEALTH_COMMAND={health['command']}")
    print(f"SMOKE_AUTHORITY={smoke['field']}")
    print(f"SMOKE_SUPPORT={smoke['support']}")
    if smoke["command"]:
        print(f"SMOKE_COMMAND={smoke['command']}")
    print(f"E2E_AUTHORITY={e2e['field']}")
    print(f"E2E_SUPPORT={e2e['support']}")
    if e2e["command"]:
        print(f"E2E_COMMAND={e2e['command']}")
    for name in ADDITIVE:
        item = report["capabilities"][name]
        print(f"CAPABILITY={name} {item['support']}")
        if item.get("command"):
            print(f"CAPABILITY_COMMAND={name} {item['command']}")
        if item.get("fuller_command"):
            print(f"CAPABILITY_FULLER={name} {item['fuller_command']}")
    print(f"FINDING_COUNT={len(findings)}")
    for item in shown:
        print(f"FINDING={item['code']} {item['detail']}")
    hidden = len(findings) - len(shown)
    if hidden:
        print(f"FINDINGS_TRUNCATED={hidden}")
        print(f"FULLER={command_for(root, 'full')}")
        print(f"RAW={command_for(root, 'raw')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Runtime and observability contract check")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--root", default=".")
    check.add_argument("--max-findings", type=int, default=DEFAULT_MAX_FINDINGS)
    mode = check.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true")
    mode.add_argument("--raw", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_findings < 0:
        fail_usage("max-findings must be non-negative")
    root = Path(args.root).resolve()
    report = check_contract(root)
    mode = "raw" if args.raw else "full" if args.full else "bounded"
    emit_check(root, report, args.max_findings, mode)
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
