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
CREDENTIAL_ACTION_MARKERS = (
    "aws-actions/configure-aws-credentials",
    "azure/login",
    "google-github-actions/auth",
)
INFRA_MUTATION_MARKERS = (
    "terraform apply",
    "pulumi up",
    "kubectl apply",
    "helm upgrade",
)
ID_TOKEN_WRITE_RE = re.compile(r"id-token\s*:\s*['\"]?write\b")
WRITE_PERMISSION_VALUES = {"write", "write-all"}
SECRET_REF_RE = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")
SECRET_BRACKET_RE = re.compile(
    r"""secrets\[\s*(?:'(?P<single>[^']*)'|"(?P<double>[^"]*)"|(?P<dynamic>[^\]]+))\s*\]"""
)
SECRETS_INHERIT_RE = re.compile(r"(?m)^[ \t]*secrets:[ \t]*inherit[ \t]*(?:#.*)?$")
BUILTIN_GITHUB_TOKEN = "GITHUB_TOKEN"
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
        "bootstrap",
        "scope",
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
        "scope",
        "approval_state",
    }
)
CANONICAL_BOOTSTRAP = "allow-no-tests"
SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$")
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
GITHUB_ORIGIN_RE = re.compile(
    r"^(?:https?://(?:[^@/]+@)?github\.com/|ssh://(?:[^@/]+@)?github\.com/|git@github\.com:)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
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


def authoritative_symlink(path: Path, root: Path) -> bool:
    """True when this authoritative path, or a parent inside the root, is a symlink."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def load_project(root: Path) -> tuple[dict[str, Any] | None, bool]:
    path = root / ".engineering" / "project.yaml"
    if authoritative_symlink(path, root):
        return None, True
    if not path.is_file():
        return None, False
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None, True
    if not isinstance(loaded, dict):
        return None, True
    return loaded, False


GOVERNANCE_WORKFLOW_PATHS = {
    ".github/workflows/engineering-system.yml",
    ".github/workflows/engineering-system.yaml",
}
CANONICAL_COMPLIANCE_USES = frozenset(
    {
        "datarelay-labs/engineering-system/.github/workflows/adoption-compliance.yml",
        "datarelay-labs/engineering-system/.github/workflows/enforcement-check.yml",
        "datarelay-labs/engineering-system/.github/workflows/affected-tests.yml",
    }
)
GOVERNANCE_TOP_LEVEL = frozenset({"name", "on", "permissions", "jobs"})
GOVERNANCE_JOB_KEYS = frozenset({"uses", "with", "permissions"})
GOVERNANCE_WITH_KEYS = frozenset({"manifest_path", "trigger"})
READ_ONLY_PERMISSION_VALUES = frozenset({"read", "none"})


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
    if path.is_symlink():
        return None
    try:
        if path.stat().st_size > MAX_WORKFLOW_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def read_only_permissions(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in READ_ONLY_PERMISSION_VALUES | {"read-all"}
    if isinstance(value, dict):
        return all(
            isinstance(item, str) and item.strip().lower() in READ_ONLY_PERMISSION_VALUES
            for item in value.values()
        )
    return False


def pull_request_trigger(loaded: dict[str, Any]) -> bool:
    """GitHub workflow `on` is boolean true under YAML 1.1."""
    if "on" in loaded:
        trigger = loaded.get("on")
    elif True in loaded:
        trigger = loaded.get(True)
    else:
        return False
    if isinstance(trigger, str):
        return trigger.strip() == "pull_request"
    if isinstance(trigger, list):
        return trigger == ["pull_request"]
    if isinstance(trigger, dict):
        return set(trigger) == {"pull_request"}
    return False


def governance_top_keys(loaded: dict[str, Any]) -> set[Any]:
    keys = set(loaded)
    if True in keys:
        keys.remove(True)
        keys.add("on")
    return keys


def canonical_compliance_use(raw: str) -> bool:
    match = REMOTE_USE_RE.fullmatch(raw.strip().strip("'\""))
    if match is None or not FULL_SHA_RE.fullmatch(match.group("ref") or ""):
        return False
    path = match.group("path")
    if not path:
        return False
    return f"{match.group('owner')}/{match.group('repo')}/{path}" in CANONICAL_COMPLIANCE_USES


def canonical_compliance_job(job: Any) -> bool:
    if not isinstance(job, dict) or set(job) - GOVERNANCE_JOB_KEYS:
        return False
    if any(key in job for key in ("runs-on", "steps", "run")):
        return False
    uses = job.get("uses")
    if not isinstance(uses, str) or not canonical_compliance_use(uses):
        return False
    inputs = job.get("with")
    if inputs is not None:
        if not isinstance(inputs, dict) or set(inputs) - GOVERNANCE_WITH_KEYS:
            return False
        if not all(isinstance(value, str) for value in inputs.values()):
            return False
    return read_only_permissions(job.get("permissions"))


def is_governance_workflow(path: Path, root: Path, text: str) -> bool:
    """Canonical compliance caller only. Local runs and other callers are product surface."""
    if path.relative_to(root).as_posix() not in GOVERNANCE_WORKFLOW_PATHS:
        return False
    if sensitive_workflow(path, text):
        return False
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    if not isinstance(loaded, dict) or governance_top_keys(loaded) - GOVERNANCE_TOP_LEVEL:
        return False
    if not pull_request_trigger(loaded) or not read_only_permissions(loaded.get("permissions")):
        return False
    jobs = loaded.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return False
    return all(canonical_compliance_job(job) for job in jobs.values())


def permission_grants_write(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in WRITE_PERMISSION_VALUES
    if isinstance(value, dict):
        return any(
            isinstance(item, str) and item.strip().lower() in WRITE_PERMISSION_VALUES
            for item in value.values()
        )
    return False


def permissions_grant_write(text: str) -> bool:
    """Workflow-level and job-level GitHub permissions only, not step inputs."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    if not isinstance(loaded, dict):
        return False
    if permission_grants_write(loaded.get("permissions")):
        return True
    jobs = loaded.get("jobs")
    if not isinstance(jobs, dict):
        return False
    return any(
        isinstance(job, dict) and permission_grants_write(job.get("permissions"))
        for job in jobs.values()
    )


def bracket_secret_is_external(match: re.Match[str]) -> bool:
    literal = match.group("single")
    if literal is None:
        literal = match.group("double")
    if literal is None:
        return True
    return literal != BUILTIN_GITHUB_TOKEN


def secret_backed_external_write(text: str) -> bool:
    """Credential forwarding or a secret other than the built-in GITHUB_TOKEN.

    Dot and quoted bracket indexes are explicit. A dynamic or unquoted index
    fails closed. This does not interpret shell.
    """
    if SECRETS_INHERIT_RE.search(text):
        return True
    if any(match.group(1) != BUILTIN_GITHUB_TOKEN for match in SECRET_REF_RE.finditer(text)):
        return True
    return any(bracket_secret_is_external(match) for match in SECRET_BRACKET_RE.finditer(text))


def sensitive_workflow(path: Path, text: str) -> bool:
    haystack = f"{path.as_posix()}\n{text}".lower()
    if any(marker in haystack for marker in SENSITIVE_MARKERS):
        return True
    if ID_TOKEN_WRITE_RE.search(haystack) or permissions_grant_write(text):
        return True
    if secret_backed_external_write(text):
        return True
    if any(marker in haystack for marker in CREDENTIAL_ACTION_MARKERS):
        return True
    return any(marker in haystack for marker in INFRA_MUTATION_MARKERS)


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


def explicit_bootstrap(fixture: dict[str, Any] | None, binding: str) -> tuple[bool, str | None]:
    """Canonical allow-no-tests evidence arrives only for the audited repository."""
    if binding != "ok" or fixture is None or "bootstrap" not in fixture:
        return False, None
    if fixture.get("bootstrap") == CANONICAL_BOOTSTRAP:
        return True, None
    return False, "bootstrap evidence is malformed"


def collect_facts(
    root: Path,
    fixture: dict[str, Any] | None = None,
    *,
    binding: str = "unavailable",
) -> dict[str, Any]:
    project, malformed = load_project(root)
    conflicts: list[str] = []
    production_oriented = None
    maturity = None
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
    for relative in (".github", ".github/workflows"):
        if not authoritative_symlink(root / relative, root):
            continue
        unreadable_workflow = True
        workflows.append(
            {
                "path": relative,
                "sensitive": True,
                "uses": [],
                "unreadable": True,
                "governance": False,
            }
        )
        break
    for path in iter_files(root):
        if is_material_code(path, root):
            material_code = True
        if is_build_file(path, root):
            build_surface = True
        if path.name in SITE_CONFIGS:
            docs_surface = True
        if not is_workflow(path, root):
            continue
        if authoritative_symlink(path, root):
            unreadable_workflow = True
            workflows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sensitive": True,
                    "uses": [],
                    "unreadable": True,
                    "governance": False,
                }
            )
            continue
        text = workflow_text(path)
        if text is None:
            unreadable_workflow = True
            workflows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sensitive": True,
                    "uses": [],
                    "unreadable": True,
                    "governance": False,
                }
            )
            continue
        lowered = text.lower()
        if docs_workflow(lowered) or "pages" in path.name.lower():
            docs_surface = True
        governance = is_governance_workflow(path, root, text)
        workflows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sensitive": sensitive_workflow(path, text),
                "uses": collect_uses(text),
                "unreadable": False,
                "governance": governance,
            }
        )
    product_workflow = any(not item["governance"] for item in workflows)
    executable_surface = material_code or build_surface or product_workflow
    pre_product, bootstrap_error = explicit_bootstrap(fixture, binding)
    if bootstrap_error:
        conflicts.append(bootstrap_error)
    elif pre_product and (executable_surface or docs_surface or production is True):
        conflicts.append("bootstrap evidence conflicts with executable, docs, or production facts")
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
        return "empty-preproduct", "explicit allow-no-tests bootstrap without executable surface"
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


def bounded_scope(value: Any) -> str | None:
    if not isinstance(value, str) or SCOPE_RE.fullmatch(value) is None:
        return None
    return value


def evidence_matches(entry: Any, control_id: str, repository: Any, scope: Any) -> bool:
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
    bound_scope = bounded_scope(entry.get("scope"))
    expected_scope = bounded_scope(scope)
    if bound_scope is None or expected_scope is None or bound_scope != expected_scope:
        return False
    return True


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _lexical_path(base: Path, raw: str) -> Path | None:
    """Join a git pointer without following symlinks. Reject escapes above the filesystem root."""
    text = raw.strip()
    if not text or any(char in text for char in "\x00\r\n"):
        return None
    if text.startswith("~"):
        return None
    incoming = Path(text)
    if incoming.is_absolute():
        parts: list[str] = []
        tokens = incoming.parts[1:]
        anchor = incoming.anchor
    else:
        parts = list(base.parts)
        tokens = incoming.parts
        anchor = ""
    for token in tokens:
        if token in {"", "."}:
            continue
        if token == "..":
            if not parts:
                return None
            parts.pop()
            continue
        if token != Path(token).name:
            return None
        parts.append(token)
    if incoming.is_absolute():
        return Path(anchor).joinpath(*parts) if parts else Path(anchor)
    if not parts:
        return None
    return Path(*parts)


def _same_lexical_path(left: Path, right: Path) -> bool:
    return _lexical_path(Path("/"), str(left)) == _lexical_path(Path("/"), str(right))


def _worktree_common_dir(admin: Path) -> Path | None:
    """Resolve worktrees/<name>/commondir only when it names that admin dir's common git dir."""
    pointer = admin / "commondir"
    if pointer.is_symlink() or not pointer.is_file():
        return None
    raw = _read_text(pointer)
    if raw is None:
        return None
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    common = _lexical_path(admin, lines[0])
    if common is None or common.is_symlink():
        return None
    name = admin.name
    if not name or name in {".", ".."}:
        return None
    if not _same_lexical_path(common / "worktrees" / name, admin):
        return None
    return common


def _regular_config_text(config_path: Path) -> str | None:
    if config_path.is_symlink() or not config_path.is_file():
        return None
    return _read_text(config_path)


def read_git_config(root: Path) -> str | None:
    git_path = root / ".git"
    if git_path.is_symlink():
        return None
    if git_path.is_dir():
        return _regular_config_text(git_path / "config")
    if not git_path.is_file():
        return None
    pointer = _read_text(git_path)
    if pointer is None:
        return None
    lines = [line for line in pointer.splitlines() if line.strip()]
    if len(lines) != 1 or not lines[0].startswith("gitdir:"):
        return None
    gitdir_raw = lines[0].split(":", 1)[1].strip()
    admin = _lexical_path(git_path.parent, gitdir_raw)
    if admin is None or admin.is_symlink() or not admin.is_dir():
        return None
    if not (admin / "commondir").is_file():
        return None
    common = _worktree_common_dir(admin)
    if common is None:
        return None
    return _regular_config_text(common / "config")


def local_repository_identity(root: Path) -> str | None:
    """Owner/repo from the local origin URL. This read does not use the network."""
    config = read_git_config(root)
    if not config:
        return None
    url = None
    in_origin = False
    for line in config.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_origin = stripped == '[remote "origin"]'
            continue
        if not in_origin or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip().lower() != "url":
            continue
        url = value.strip().strip('"').strip("'")
        break
    if not url:
        return None
    match = GITHUB_ORIGIN_RE.fullmatch(url)
    if match is None:
        return None
    slug = f"{match.group('owner')}/{match.group('repo')}"
    if REPO_RE.fullmatch(slug) is None:
        return None
    return slug


def github_fixture_binding(root: Path, fixture: dict[str, Any] | None) -> str:
    """ok when fixture repository matches local origin; otherwise mismatch or unavailable."""
    if not fixture:
        return "unavailable"
    local = local_repository_identity(root)
    claimed = fixture.get("repository")
    if local is None or not isinstance(claimed, str) or REPO_RE.fullmatch(claimed) is None:
        return "unavailable"
    if claimed != local:
        return "mismatch"
    return "ok"


def matching_evidence(fixture: dict[str, Any] | None, control_id: str) -> bool:
    if not fixture:
        return False
    entries = fixture.get("equivalent_external")
    if entries is None:
        return False
    if not isinstance(entries, list):
        return False
    repository = fixture.get("repository")
    scope = fixture.get("scope")
    return any(evidence_matches(entry, control_id, repository, scope) for entry in entries)


def unbound_repository_fact(binding: str) -> dict[str, str] | None:
    """Repository-scoped fixture facts are unused unless binding is ok."""
    if binding == "ok":
        return None
    if binding == "mismatch":
        detail = "fixture repository does not match the audited repository"
    else:
        detail = "audited repository identity is unavailable"
    return {"detail": detail}


def github_control_state(
    control_id: str,
    profile: str,
    fixture: dict[str, Any] | None,
    visibility_known: bool,
    binding: str,
) -> dict[str, str]:
    requirement = requirement_for(profile, control_id, privileged_declared=False)
    if requirement == "DEFERRED":
        return control(control_id, "DEFERRED", "empty/preproduct control is explicitly deferred")
    if requirement == "NOT_APPLICABLE":
        return control(control_id, "NOT_APPLICABLE", "control does not apply to this profile")
    if requirement == "UNKNOWN":
        return control(control_id, "UNKNOWN", "profile is NEEDS_INPUT")
    if fixture is None:
        return control(control_id, "UNAVAILABLE", "github fixture is absent")
    unbound = unbound_repository_fact(binding)
    if unbound is not None:
        return control(control_id, "UNAVAILABLE", unbound["detail"])
    if visibility_known and matching_evidence(fixture, control_id):
        return control(control_id, "EQUIVALENT_EXTERNAL", "bounded external evidence matches the repository and scope")
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


def visibility_state(profile: str, fixture: dict[str, Any] | None, binding: str) -> dict[str, str]:
    requirement = requirement_for(profile, "repository_visibility", privileged_declared=False)
    if requirement == "DEFERRED":
        return control("repository_visibility", "DEFERRED", "empty/preproduct control is explicitly deferred")
    if requirement == "UNKNOWN":
        return control("repository_visibility", "UNKNOWN", "profile is NEEDS_INPUT")
    if fixture is None or "visibility" not in fixture:
        return control("repository_visibility", "UNAVAILABLE", "repository visibility is inaccessible")
    unbound = unbound_repository_fact(binding)
    if unbound is not None:
        return control("repository_visibility", "UNAVAILABLE", unbound["detail"])
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


def well_formed_tool(item: Any) -> bool:
    if not isinstance(item, dict) or set(item) - TOOL_KEYS:
        return False
    if not isinstance(item.get("privileged"), bool):
        return False
    return bounded_text(item.get("id"), 80) is not None


def privileged_tool_status(fixture: dict[str, Any] | None) -> str:
    """absent, none, known, unknown, or malformed. Malformed never means no tool."""
    if fixture is None or "privileged_tools" not in fixture:
        return "absent"
    tools = fixture.get("privileged_tools")
    if not isinstance(tools, list):
        return "malformed"
    saw_privileged = False
    for item in tools:
        if not well_formed_tool(item):
            return "malformed"
        if item["privileged"] is not True:
            continue
        saw_privileged = True
        if not tool_known(item):
            return "unknown"
    if saw_privileged:
        return "known"
    return "none"


def tool_known(item: dict[str, Any]) -> bool:
    if set(item) - TOOL_KEYS:
        return False
    provenance = item.get("provenance")
    provider = bounded_text(item.get("provider"), 100)
    identity = bounded_text(item.get("immutable_id"), 120) or bounded_text(item.get("version"), 80)
    if not isinstance(provenance, str) or provenance.strip().lower() in UNKNOWN_PROVENANCE:
        return False
    return bool(provider and identity)


def provenance_state(profile: str, fixture: dict[str, Any] | None, binding: str) -> dict[str, str]:
    if fixture is not None and binding != "ok":
        if profile == "NEEDS_INPUT":
            return control("privileged_tool_provenance", "UNKNOWN", "profile is NEEDS_INPUT")
        rejected = unbound_repository_fact(binding)
        detail = "audited repository identity is unavailable" if rejected is None else rejected["detail"]
        return control("privileged_tool_provenance", "UNAVAILABLE", detail)
    status = privileged_tool_status(fixture)
    if status == "malformed":
        return control(
            "privileged_tool_provenance",
            "REQUIRED_FAIL",
            "malformed privileged tool entry fails closed",
        )
    if status == "unknown":
        return control(
            "privileged_tool_provenance",
            "REQUIRED_FAIL",
            "unknown privileged tool, MCP, or plugin provenance fails closed",
        )
    if profile == "empty-preproduct":
        return control("privileged_tool_provenance", "DEFERRED", "empty/preproduct control is explicitly deferred")
    if profile == "NEEDS_INPUT":
        return control("privileged_tool_provenance", "UNKNOWN", "profile is NEEDS_INPUT")
    if status == "absent":
        return control("privileged_tool_provenance", "UNAVAILABLE", "privileged tool facts are inaccessible")
    if status == "none":
        return control("privileged_tool_provenance", "NOT_APPLICABLE", "no privileged tool is declared")
    return control("privileged_tool_provenance", "REQUIRED_PASS", "privileged tool provenance is explicit")


def visibility_known(fixture: dict[str, Any] | None) -> bool:
    if not fixture:
        return False
    return fixture.get("visibility") in KNOWN_VISIBILITY


def build_report(root: Path, fixture: dict[str, Any] | None) -> dict[str, Any]:
    binding = github_fixture_binding(root, fixture)
    facts = collect_facts(root, fixture, binding=binding)
    profile, reason = classify_profile(facts)
    sensitive_fail, sensitive_pass, ordinary_fail, ordinary_pass = pin_buckets(facts)
    known_visibility = visibility_known(fixture)
    controls = [
        visibility_state(profile, fixture, binding),
        *[
            github_control_state(control_id, profile, fixture, known_visibility, binding)
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
        provenance_state(profile, fixture, binding),
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
    classify.add_argument("--github-fixture")
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
