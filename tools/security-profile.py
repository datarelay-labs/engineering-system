#!/usr/bin/env python3
"""Read-only repository security-profile classifier and control auditor.

The evaluator reads the target worktree and an optional normalized fixture.
It does not access the network, mutate GitHub settings, or rewrite workflows.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "security-profile.schema.json"

PROFILES = (
    "production-code",
    "development-code",
    "docs-site",
    "empty-preproduct",
    "NEEDS_INPUT",
)
CONTROL_STATES = (
    "REQUIRED_PASS",
    "REQUIRED_FAIL",
    "RECOMMENDED_PASS",
    "RECOMMENDED_GAP",
    "EQUIVALENT_EXTERNAL",
    "DEFERRED",
    "UNAVAILABLE",
    "NOT_APPLICABLE",
    "UNKNOWN",
)
CONTROL_IDS = (
    "repository_visibility",
    "secret_scanning",
    "push_protection",
    "dependabot_security_updates",
    "codeql_or_sast",
    "sensitive_action_pin",
    "ordinary_action_pin",
    "privileged_tool_provenance",
)
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "venv",
}
CODE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".php",
    ".cs",
    ".kt",
    ".swift",
    ".c",
    ".cc",
    ".cpp",
    ".h",
}
SITE_CONFIGS = {
    "mkdocs.yml",
    "docusaurus.config.js",
    "docusaurus.config.ts",
    "astro.config.mjs",
    "astro.config.js",
    "hugo.toml",
    "_config.yml",
    "mint.json",
}
SITE_HELPERS = SITE_CONFIGS | {"conf.py"}
BUILD_FILES = {
    "Dockerfile",
    "package.json",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "Makefile",
    "requirements.txt",
}
NON_PRODUCT_PARTS = {"docs", "site", "documentation", ".github", ".engineering"}
SENSITIVE_MARKERS = (
    "security",
    "release",
    "publish",
    "deploy",
    "pages",
    "signing",
    "provenance",
    "production",
)
PAGES_MARKERS = ("deploy-pages", "upload-pages-artifact", "configure-pages")
FACT_KEY = {
    "secret_scanning": "secret_scanning",
    "push_protection": "push_protection",
    "dependabot_security_updates": "dependabot_security_updates",
    "codeql_or_sast": "codeql_default_setup",
}
ENABLED_VALUES = {
    "secret_scanning": {"enabled"},
    "push_protection": {"enabled"},
    "dependabot_security_updates": {"enabled"},
    "codeql_or_sast": {"configured"},
}
DISABLED_VALUES = {
    "secret_scanning": {"disabled"},
    "push_protection": {"disabled"},
    "dependabot_security_updates": {"disabled"},
    "codeql_or_sast": {"not-configured"},
}
ALLOWED_FIXTURE_KEYS = frozenset(
    {
        "repository",
        "visibility",
        "secret_scanning",
        "push_protection",
        "dependabot_security_updates",
        "codeql_default_setup",
        "equivalent_external",
        "privileged_tools",
    }
)
EVIDENCE_KEYS = frozenset(
    {
        "control_id",
        "provider",
        "immutable_id",
        "version",
        "evidence_ref",
        "timestamp",
        "repository",
        "approval_state",
    }
)
TOOL_KEYS = frozenset(
    {"id", "privileged", "provenance", "provider", "immutable_id", "version"}
)
KNOWN_VISIBILITY = {"public", "private", "internal"}
UNKNOWN_PROVENANCE = {"", "unknown", "unapproved"}
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
LOCAL_USE_RE = re.compile(r"^\./[A-Za-z0-9_./-]+$")
REMOTE_USE_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"(?:/(?P<path>[A-Za-z0-9_./-]+))?@(?P<ref>[A-Za-z0-9_./+-]+)$"
)
USE_LINE_RE = re.compile(r"(?m)^[ \t]*-?[ \t]*uses:[ \t]*['\"]?([^'\"\s#]+)")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_WORKFLOW_BYTES = 1_000_000


class ProfileError(Exception):
    """Caller input cannot be evaluated without guessing."""


def iter_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for name in filenames:
            found.append(Path(dirpath) / name)
    return found


def load_project(root: Path) -> tuple[dict[str, Any] | None, bool]:
    path = root / ".engineering" / "project.yaml"
    if not path.is_file():
        return None, False
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None, True
    if not isinstance(loaded, dict):
        return None, True
    return loaded, False


def is_workflow(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return (
        len(rel.parts) >= 3
        and rel.parts[0] == ".github"
        and rel.parts[1] == "workflows"
        and path.suffix in {".yml", ".yaml"}
    )


def is_material_code(path: Path, root: Path) -> bool:
    if path.suffix.lower() not in CODE_SUFFIXES or path.name in SITE_HELPERS:
        return False
    rel = path.relative_to(root)
    return not (set(rel.parts[:-1]) & NON_PRODUCT_PARTS)


def is_build_file(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if set(rel.parts[:-1]) & {"docs", "site", "documentation", ".engineering"}:
        return False
    return path.name in BUILD_FILES


def workflow_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_WORKFLOW_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def sensitive_workflow(path: Path, text: str) -> bool:
    haystack = f"{path.as_posix()}\n{text}".lower()
    return any(marker in haystack for marker in SENSITIVE_MARKERS)


def docs_workflow(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in PAGES_MARKERS)


def classify_use(raw: str) -> dict[str, str]:
    if raw.startswith("./"):
        if LOCAL_USE_RE.fullmatch(raw):
            return {"kind": "local", "verdict": "local", "ref": raw}
        return {"kind": "malformed", "verdict": "fail", "ref": raw}
    if raw.startswith(("docker://", "http://", "https://")):
        return {"kind": "unsupported", "verdict": "fail", "ref": raw}
    match = REMOTE_USE_RE.fullmatch(raw)
    if match is None:
        return {"kind": "malformed", "verdict": "fail", "ref": raw}
    ref = match.group("ref")
    if FULL_SHA_RE.fullmatch(ref):
        return {"kind": "remote", "verdict": "pass", "ref": ref}
    return {"kind": "remote", "verdict": "fail", "ref": ref}


def collect_uses(text: str) -> list[dict[str, str]]:
    return [classify_use(match.group(1).strip()) for match in USE_LINE_RE.finditer(text)]


def collect_facts(root: Path) -> dict[str, Any]:
    project, malformed = load_project(root)
    conflicts: list[str] = []
    production_oriented = None
    maturity = None
    pre_product = False
    if isinstance(project, dict):
        operations = project.get("operations")
        project_meta = project.get("project")
        if operations is not None and not isinstance(operations, dict):
            malformed = True
        if project_meta is not None and not isinstance(project_meta, dict):
            malformed = True
        if isinstance(operations, dict):
            if "production_oriented" in operations:
                value = operations.get("production_oriented")
                if isinstance(value, bool):
                    production_oriented = value
                else:
                    conflicts.append("production_oriented is not a boolean")
            if operations.get("pre_product") is True:
                pre_product = True
        if isinstance(project_meta, dict) and "maturity" in project_meta:
            maturity = project_meta.get("maturity")
            if not isinstance(maturity, str):
                conflicts.append("maturity is not a string")
                maturity = None
    production_signals = []
    nonproduction_signals = []
    if production_oriented is True:
        production_signals.append("production_oriented")
    if production_oriented is False:
        nonproduction_signals.append("production_oriented")
    if maturity == "production":
        production_signals.append("maturity")
    if maturity in {"experimental", "development", "maintenance"}:
        nonproduction_signals.append("maturity")
    if production_signals and nonproduction_signals:
        conflicts.append("production posture signals disagree")
    if production_signals and not nonproduction_signals:
        production: bool | None = True
    elif nonproduction_signals and not production_signals:
        production = False
    else:
        production = None

    material_code = False
    build_surface = False
    docs_surface = False
    workflows: list[dict[str, Any]] = []
    unreadable_workflow = False
    for path in iter_files(root):
        if is_material_code(path, root):
            material_code = True
        if is_build_file(path, root):
            build_surface = True
        if path.name in SITE_CONFIGS:
            docs_surface = True
        if not is_workflow(path, root):
            continue
        text = workflow_text(path)
        if text is None:
            unreadable_workflow = True
            workflows.append({"path": path.relative_to(root).as_posix(), "sensitive": True, "uses": [], "unreadable": True})
            continue
        lowered = text.lower()
        if docs_workflow(lowered) or "pages" in path.name.lower():
            docs_surface = True
        workflows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sensitive": sensitive_workflow(path, text),
                "uses": collect_uses(text),
                "unreadable": False,
            }
        )
    executable_surface = material_code or build_surface or bool(workflows)
    if pre_product and (executable_surface or docs_surface or production is True):
        conflicts.append("pre_product conflicts with executable, docs, or production facts")
    return {
        "malformed_project": malformed,
        "conflicts": conflicts,
        "production": production,
        "pre_product": pre_product,
        "material_code": material_code,
        "docs_surface": docs_surface,
        "executable_surface": executable_surface,
        "workflows": workflows,
        "unreadable_workflow": unreadable_workflow,
    }


def classify_profile(facts: dict[str, Any]) -> tuple[str, str]:
    if facts["malformed_project"]:
        return "NEEDS_INPUT", "insufficient distinguishing facts: project profile is malformed"
    if facts["conflicts"]:
        return "NEEDS_INPUT", "conflicting required facts: " + "; ".join(facts["conflicts"])
    if facts["production"] is True and facts["executable_surface"]:
        return "production-code", "explicit production posture with executable surface"
    if facts["pre_product"] and not facts["executable_surface"]:
        return "empty-preproduct", "explicit pre-product bootstrap without executable surface"
    if facts["docs_surface"] and not facts["material_code"]:
        return "docs-site", "docs or site build without material product code"
    if facts["material_code"] and facts["production"] is False:
        return "development-code", "code-bearing non-production repository"
    return "NEEDS_INPUT", "insufficient distinguishing facts"


def requirement_for(profile: str, control_id: str, *, privileged_declared: bool) -> str:
    if profile == "NEEDS_INPUT":
        return "UNKNOWN"
    if profile == "empty-preproduct":
        return "DEFERRED"
    if control_id == "codeql_or_sast" and profile == "docs-site":
        return "NOT_APPLICABLE"
    if control_id == "ordinary_action_pin" and profile == "docs-site":
        return "NOT_APPLICABLE"
    if control_id == "privileged_tool_provenance":
        if not privileged_declared:
            return "NOT_APPLICABLE"
        return "REQUIRED"
    if control_id == "sensitive_action_pin":
        return "REQUIRED"
    if control_id == "ordinary_action_pin":
        return "RECOMMENDED"
    if profile == "production-code":
        return "REQUIRED"
    return "RECOMMENDED"


def control(control_id: str, state: str, detail: str) -> dict[str, str]:
    if state not in CONTROL_STATES:
        raise ProfileError(f"unknown control state {state}")
    return {"id": control_id, "state": state, "detail": detail[:400]}


def bounded_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > limit or any(char in stripped for char in "\r\n"):
        return None
    return stripped


def evidence_matches(entry: Any, control_id: str, repository: Any) -> bool:
    if not isinstance(entry, dict) or set(entry) - EVIDENCE_KEYS:
        return False
    if entry.get("control_id") != control_id:
        return False
    provider = bounded_text(entry.get("provider"), 100)
    evidence_ref = bounded_text(entry.get("evidence_ref"), 200)
    timestamp = entry.get("timestamp")
    approval = entry.get("approval_state")
    identity = bounded_text(entry.get("immutable_id"), 120) or bounded_text(entry.get("version"), 80)
    bound_repo = entry.get("repository")
    if not provider or not evidence_ref or not identity:
        return False
    if not isinstance(timestamp, str) or TIMESTAMP_RE.fullmatch(timestamp) is None:
        return False
    if approval != "approved":
        return False
    if not isinstance(bound_repo, str) or REPO_RE.fullmatch(bound_repo) is None:
        return False
    if not isinstance(repository, str) or bound_repo != repository:
        return False
    return True


def matching_evidence(fixture: dict[str, Any] | None, control_id: str) -> bool:
    if not fixture:
        return False
    entries = fixture.get("equivalent_external")
    if entries is None:
        return False
    if not isinstance(entries, list):
        return False
    repository = fixture.get("repository")
    return any(evidence_matches(entry, control_id, repository) for entry in entries)


def github_control_state(
    control_id: str,
    profile: str,
    fixture: dict[str, Any] | None,
    visibility_known: bool,
) -> dict[str, str]:
    requirement = requirement_for(profile, control_id, privileged_declared=privileged_declared)
    if requirement == "DEFERRED":
        return control(control_id, "DEFERRED", "empty/preproduct control is explicitly deferred")
    if requirement == "NOT_APPLICABLE":
        return control(control_id, "NOT_APPLICABLE", "control does not apply to this profile")
    if requirement == "UNKNOWN":
        return control(control_id, "UNKNOWN", "profile is NEEDS_INPUT")
    if fixture is None:
        return control(control_id, "UNAVAILABLE", "github fixture is absent")
    if visibility_known and matching_evidence(fixture, control_id):
        return control(control_id, "EQUIVALENT_EXTERNAL", "bounded external evidence matches the repository")
    if not visibility_known and matching_evidence(fixture, control_id):
        return control(control_id, "UNAVAILABLE", "unknown visibility cannot satisfy a control")
    fact_key = FACT_KEY[control_id]
    if fact_key not in fixture or fixture.get(fact_key) in (None, "", "unavailable"):
        return control(control_id, "UNAVAILABLE", "github setting is inaccessible")
    fact = fixture.get(fact_key)
    if not isinstance(fact, str):
        return control(control_id, "UNKNOWN", "github setting value is not a string")
    if fact in ENABLED_VALUES[control_id]:
        if not visibility_known:
            return control(control_id, "UNAVAILABLE", "unknown visibility never becomes PASS")
        state = "REQUIRED_PASS" if requirement == "REQUIRED" else "RECOMMENDED_PASS"
        return control(control_id, state, "github setting is enabled")
    if fact in DISABLED_VALUES[control_id]:
        state = "REQUIRED_FAIL" if requirement == "REQUIRED" else "RECOMMENDED_GAP"
        return control(control_id, state, "github setting is not enabled")
    return control(control_id, "UNKNOWN", "github setting value is unrecognized")


def visibility_state(profile: str, fixture: dict[str, Any] | None) -> dict[str, str]:
    requirement = requirement_for(profile, "repository_visibility", privileged_declared=False)
    if requirement == "DEFERRED":
        return control("repository_visibility", "DEFERRED", "empty/preproduct control is explicitly deferred")
    if requirement == "UNKNOWN":
        return control("repository_visibility", "UNKNOWN", "profile is NEEDS_INPUT")
    if fixture is None or "visibility" not in fixture:
        return control("repository_visibility", "UNAVAILABLE", "repository visibility is inaccessible")
    visibility = fixture.get("visibility")
    if visibility in KNOWN_VISIBILITY:
        state = "REQUIRED_PASS" if requirement == "REQUIRED" else "RECOMMENDED_PASS"
        return control("repository_visibility", state, "repository visibility is explicit")
    return control("repository_visibility", "UNAVAILABLE", "repository visibility is unknown")


def pin_buckets(facts: dict[str, Any]) -> tuple[list[str], list[str], list[str], list[str]]:
    sensitive_fail: list[str] = []
    sensitive_pass: list[str] = []
    ordinary_fail: list[str] = []
    ordinary_pass: list[str] = []
    for workflow in facts["workflows"]:
        if workflow["unreadable"]:
            sensitive_fail.append(f"{workflow['path']}: unreadable")
            continue
        for use in workflow["uses"]:
            if use["verdict"] == "local":
                continue
            label = f"{workflow['path']}:{use['kind']}"
            if workflow["sensitive"]:
                if use["verdict"] == "pass":
                    sensitive_pass.append(label)
                else:
                    sensitive_fail.append(label)
            elif use["verdict"] == "pass":
                ordinary_pass.append(label)
            else:
                ordinary_fail.append(label)
    return sensitive_fail, sensitive_pass, ordinary_fail, ordinary_pass


def pin_state(control_id: str, profile: str, fails: list[str], passes: list[str], *, unreadable: bool) -> dict[str, str]:
    requirement = requirement_for(profile, control_id, privileged_declared=False)
    if requirement == "DEFERRED":
        return control(control_id, "DEFERRED", "empty/preproduct control is explicitly deferred")
    if requirement == "NOT_APPLICABLE":
        return control(control_id, "NOT_APPLICABLE", "control does not apply to this profile")
    if requirement == "UNKNOWN":
        return control(control_id, "UNKNOWN", "profile is NEEDS_INPUT")
    if unreadable or fails:
        state = "REQUIRED_FAIL" if requirement == "REQUIRED" else "RECOMMENDED_GAP"
        kind = "malformed, unreadable, or non-SHA remote action"
        return control(control_id, state, kind)
    if not passes:
        if requirement == "REQUIRED":
            return control(control_id, "NOT_APPLICABLE", "no remote action on this path")
        return control(control_id, "RECOMMENDED_PASS", "no remote action on this path")
    state = "REQUIRED_PASS" if requirement == "REQUIRED" else "RECOMMENDED_PASS"
    return control(control_id, state, "remote actions use full 40-hex SHAs")


def privileged_declared(fixture: dict[str, Any] | None) -> bool | None:
    """True when a privileged tool is listed, False when the list is empty, None when inaccessible."""
    if fixture is None or "privileged_tools" not in fixture:
        return None
    tools = fixture.get("privileged_tools")
    if not isinstance(tools, list):
        return None
    return any(isinstance(item, dict) and item.get("privileged") is True for item in tools)


def tool_known(item: dict[str, Any]) -> bool:
    if set(item) - TOOL_KEYS:
        return False
    provenance = item.get("provenance")
    provider = bounded_text(item.get("provider"), 100)
    identity = bounded_text(item.get("immutable_id"), 120) or bounded_text(item.get("version"), 80)
    if not isinstance(provenance, str) or provenance.strip().lower() in UNKNOWN_PROVENANCE:
        return False
    return bool(provider and identity)


def provenance_state(profile: str, fixture: dict[str, Any] | None) -> dict[str, str]:
    declared = privileged_declared(fixture)
    if declared is None:
        if profile == "empty-preproduct":
            return control("privileged_tool_provenance", "DEFERRED", "empty/preproduct control is explicitly deferred")
        if profile == "NEEDS_INPUT":
            return control("privileged_tool_provenance", "UNKNOWN", "profile is NEEDS_INPUT")
        return control("privileged_tool_provenance", "UNAVAILABLE", "privileged tool facts are inaccessible")
    requirement = requirement_for(profile, "privileged_tool_provenance", privileged_declared=declared)
    if requirement == "DEFERRED":
        return control("privileged_tool_provenance", "DEFERRED", "empty/preproduct control is explicitly deferred")
    if requirement == "NOT_APPLICABLE":
        return control("privileged_tool_provenance", "NOT_APPLICABLE", "no privileged tool is declared")
    if requirement == "UNKNOWN":
        return control("privileged_tool_provenance", "UNKNOWN", "profile is NEEDS_INPUT")
    tools = fixture.get("privileged_tools") if fixture else None
    privileged = [item for item in tools if isinstance(item, dict) and item.get("privileged") is True]
    if any(not tool_known(item) for item in privileged):
        return control(
            "privileged_tool_provenance",
            "REQUIRED_FAIL",
            "unknown privileged tool, MCP, or plugin provenance fails closed",
        )
    return control("privileged_tool_provenance", "REQUIRED_PASS", "privileged tool provenance is explicit")


def visibility_known(fixture: dict[str, Any] | None) -> bool:
    if not fixture:
        return False
    return fixture.get("visibility") in KNOWN_VISIBILITY


def build_report(root: Path, fixture: dict[str, Any] | None) -> dict[str, Any]:
    facts = collect_facts(root)
    profile, reason = classify_profile(facts)
    sensitive_fail, sensitive_pass, ordinary_fail, ordinary_pass = pin_buckets(facts)
    known_visibility = visibility_known(fixture)
    controls = [
        visibility_state(profile, fixture),
        *[
            github_control_state(control_id, profile, fixture, known_visibility)
            for control_id in (
                "secret_scanning",
                "push_protection",
                "dependabot_security_updates",
                "codeql_or_sast",
            )
        ],
        pin_state(
            "sensitive_action_pin",
            profile,
            sensitive_fail,
            sensitive_pass,
            unreadable=facts["unreadable_workflow"],
        ),
        pin_state("ordinary_action_pin", profile, ordinary_fail, ordinary_pass, unreadable=False),
        provenance_state(profile, fixture),
    ]
    report = {
        "profile": profile,
        "reason": reason,
        "controls": controls,
        "mutation": "NONE",
        "network": "NONE",
    }
    Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))).validate(report)
    return report


def load_fixture(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileError("github fixture is unreadable") from exc
    if not isinstance(loaded, dict):
        raise ProfileError("github fixture must be a JSON object")
    unknown = set(loaded) - ALLOWED_FIXTURE_KEYS
    if unknown:
        raise ProfileError("github fixture contains unsupported fields")
    return loaded


def command_names() -> tuple[str, ...]:
    return ("classify", "audit")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only repository security profile audit")
    sub = parser.add_subparsers(dest="command", required=True)
    classify = sub.add_parser("classify")
    classify.add_argument("--root", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--root", required=True)
    audit.add_argument("--github-fixture")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print("security profile root is not a directory", file=sys.stderr)
        return 1
    fixture = None
    if getattr(args, "github_fixture", None):
        try:
            fixture = load_fixture(Path(args.github_fixture))
        except ProfileError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    try:
        report = build_report(root, fixture)
    except ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
