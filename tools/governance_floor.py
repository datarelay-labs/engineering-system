#!/usr/bin/env python3
"""Base-owned fail-closed governance floor for Engineering System pull requests."""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from pathlib import Path

import yaml

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MANAGED_EXECUTION_SURFACES = (
    "AGENTS.md",
    "tools/context_epoch.py",
    "tools/engineering-context.py",
)
PROTECTED_GOVERNANCE_SURFACES = (
    # Read-only orientation remains protected by policy epoch. Runtime-selection
    # authority is migrated separately through the root-bound profile bridge.
    "tools/engineering-context.py",
)
RETIRED_AGENT_ARTIFACTS = (".cursor", ".cursorignore", ".cursorrules")
GOVERNANCE_HELPER = "tools/governance_floor.py"
GOVERNANCE_REUSABLE_WORKFLOW = ".github/workflows/governance-floor.yml"
GOVERNANCE_DEPENDENCY_MANIFEST = ".engineering/requirements-engineering-system.txt"
ROOT_MIGRATION_MANIFEST = ".engineering/governance-migration.yaml"
EXECUTION_PROFILE_PATH = ".engineering/execution-profile.yaml"
EXECUTION_PROFILE_SCHEMA_PATH = "schemas/execution-profile.schema.json"
EXECUTION_PROFILE_HELPER_PATH = "tools/execution_profile.py"
EXECUTION_PROFILE_BOOTSTRAP_SURFACES = (
    EXECUTION_PROFILE_PATH,
    EXECUTION_PROFILE_SCHEMA_PATH,
    EXECUTION_PROFILE_HELPER_PATH,
)
EPOCH_GUARDED_GOVERNANCE_SURFACES = (
    GOVERNANCE_HELPER,
    GOVERNANCE_DEPENDENCY_MANIFEST,
    "tools/context_epoch.py",
)
CANONICAL_EPOCH_GUARDED_GOVERNANCE_SURFACES = (
    GOVERNANCE_REUSABLE_WORKFLOW,
    ".github/workflows/adoption-compliance.yml",
    "tools/check-adoption.py",
    "tools/adopt.py",
    "tools/upgrade-adoption.py",
)
PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RUNTIME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
EFFECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MANDATORY_TRUSTED_BOUNDARY_EFFECTS = frozenset(
    {
        "production",
        "destructive",
        "credential",
        "permission_boundary",
        "irreversible_publication",
        "release_authority",
    }
)
ADOPTED_ENGINEERING_WORKFLOW = ".github/workflows/engineering-system.yml"
CANONICAL_VALIDATE_WORKFLOW = ".github/workflows/validate.yml"
GOVERNANCE_WORKFLOW_PREFIX = (
    "datarelay-labs/engineering-system/.github/workflows/governance-floor.yml@"
)
ADOPTION_WORKFLOW_PREFIX = (
    "datarelay-labs/engineering-system/.github/workflows/adoption-compliance.yml@"
)
ENFORCEMENT_WORKFLOW_PREFIX = (
    "datarelay-labs/engineering-system/.github/workflows/enforcement-check.yml@"
)
AFFECTED_WORKFLOW_PREFIX = (
    "datarelay-labs/engineering-system/.github/workflows/affected-tests.yml@"
)
EXPECTED_FLOOR_CONDITION = "github.event_name == 'pull_request_target'"
EXPECTED_PR_CONDITION = "github.event_name == 'pull_request'"
EXPECTED_BASE_INPUT = "${{ github.event.pull_request.base.sha }}"
EXPECTED_HEAD_INPUT = "${{ github.event.pull_request.head.sha }}"
EXPECTED_CANONICAL_BASE_REF = (
    "${{ github.event_name == 'pull_request_target' && "
    "github.event.pull_request.base.sha || inputs.base_sha }}"
)
EXPECTED_CANONICAL_HEAD_REF = (
    "${{ github.event_name == 'pull_request_target' && "
    "github.event.pull_request.head.sha || inputs.head_sha }}"
)
RETIRED_AGENTS_RE = re.compile(
    r"(?i)(?:"
    r"IMPLEMENTER\s*=\s*CURSOR|"
    r"cursor[-_ ]?agent|"
    r"\bagent\s+persist\b|"
    r"/work-resume\b|"
    r"\.cursor(?:/|\b)|"
    r"\bcursor\s+(?:adapter|session|implementation|implementer|worker)\b|"
    r"\b(?:start|resume|launch|wait\s+for|hand\s+off\s+to)\s+(?:the\s+)?cursor\b"
    r")"
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _commit(root: Path, ref: str, label: str) -> str:
    result = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    value = result.stdout.strip()
    if result.returncode or FULL_SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{label} ref is not an exact commit")
    return value


def _read_at(root: Path, ref: str, path: str) -> str | None:
    result = _git(root, "show", f"{ref}:{path}")
    return result.stdout if result.returncode == 0 else None


def _blob_sha(root: Path, ref: str, path: str) -> str | None:
    result = _git(root, "rev-parse", "--verify", f"{ref}:{path}")
    value = result.stdout.strip()
    return value if result.returncode == 0 and FULL_SHA_RE.fullmatch(value) else None


def _tree_has_path(root: Path, ref: str, path: str) -> bool:
    result = _git(root, "ls-tree", "-r", "--name-only", ref, "--", path)
    return result.returncode == 0 and bool(result.stdout.strip())


def _profile(text: str | None, label: str) -> dict[str, object]:
    if text is None:
        raise ValueError(f"{label} .engineering/project.yaml is missing")
    try:
        payload = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{label} .engineering/project.yaml is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} .engineering/project.yaml is invalid")
    return payload


def _policy_epoch(profile: dict[str, object], label: str) -> int:
    engineering = profile.get("engineering_system") or {}
    value = engineering.get("policy_epoch", 0) if isinstance(engineering, dict) else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} engineering_system.policy_epoch is invalid")
    return value


def _profile_string_list(
    value: object, label: str, pattern: re.Pattern[str]
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label}_INVALID")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or pattern.fullmatch(item) is None:
            raise ValueError(f"{label}_INVALID")
        if item in out:
            raise ValueError(f"{label}_DUPLICATE")
        out.append(item)
    return tuple(out)


def _bootstrap_execution_profile(text: str) -> dict[str, object]:
    """Validate the first provider-neutral profile as base-owned data only.

    The bridge never imports candidate-controlled helpers. It validates the
    bootstrap profile structure itself, then root-migration evidence binds the
    exact profile/helper/schema/context/adoption blobs reviewed by the owner.
    """
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ValueError("EXECUTION_PROFILE_YAML_INVALID") from exc
    if not isinstance(raw, dict):
        raise ValueError("EXECUTION_PROFILE_INVALID")
    expected = {
        "contract_version",
        "profile_id",
        "revision",
        "runtime",
        "packet_compatibility",
        "retired_surface",
        "effect_policy",
        "policy_migration",
    }
    if set(raw) != expected:
        raise ValueError("EXECUTION_PROFILE_KEYS_INVALID")
    if raw.get("contract_version") != 1:
        raise ValueError("EXECUTION_PROFILE_CONTRACT_VERSION_INVALID")
    profile_id = raw.get("profile_id")
    if not isinstance(profile_id, str) or PROFILE_ID_RE.fullmatch(profile_id) is None:
        raise ValueError("EXECUTION_PROFILE_ID_INVALID")
    revision = raw.get("revision")
    if isinstance(revision, bool) or revision != 1:
        raise ValueError("EXECUTION_PROFILE_BOOTSTRAP_REVISION_INVALID")

    runtime = raw.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != {
        "primary",
        "optional_reviewers",
        "disabled",
    }:
        raise ValueError("EXECUTION_PROFILE_RUNTIME_INVALID")
    primary = runtime.get("primary")
    if not isinstance(primary, str) or RUNTIME_ID_RE.fullmatch(primary) is None:
        raise ValueError("EXECUTION_PROFILE_PRIMARY_INVALID")
    reviewers = _profile_string_list(
        runtime.get("optional_reviewers"),
        "EXECUTION_PROFILE_REVIEWERS",
        RUNTIME_ID_RE,
    )
    disabled = _profile_string_list(
        runtime.get("disabled"), "EXECUTION_PROFILE_DISABLED", RUNTIME_ID_RE
    )
    if primary in disabled or set(reviewers) & set(disabled):
        raise ValueError("EXECUTION_PROFILE_RUNTIME_CONFLICT")

    compatibility = raw.get("packet_compatibility")
    if not isinstance(compatibility, dict) or set(compatibility) != {
        "legacy_v2_implementers"
    }:
        raise ValueError("EXECUTION_PROFILE_COMPATIBILITY_INVALID")
    legacy = compatibility.get("legacy_v2_implementers")
    if not isinstance(legacy, dict) or not legacy:
        raise ValueError("EXECUTION_PROFILE_LEGACY_COMPATIBILITY_INVALID")
    for implementer, target in legacy.items():
        if (
            not isinstance(implementer, str)
            or RUNTIME_ID_RE.fullmatch(implementer) is None
            or target != profile_id
        ):
            raise ValueError("EXECUTION_PROFILE_LEGACY_COMPATIBILITY_INVALID")
    if legacy.get(primary) != profile_id:
        raise ValueError("EXECUTION_PROFILE_PRIMARY_COMPATIBILITY_MISSING")

    retired = raw.get("retired_surface")
    if not isinstance(retired, dict) or set(retired) != {
        "artifact_paths",
        "text_patterns",
        "remove_exact_text",
    }:
        raise ValueError("EXECUTION_PROFILE_RETIRED_SURFACE_INVALID")
    artifacts = retired.get("artifact_paths")
    if not isinstance(artifacts, list) or any(
        not isinstance(item, str)
        or not item
        or Path(item).is_absolute()
        or ".." in Path(item).parts
        for item in artifacts
    ):
        raise ValueError("EXECUTION_PROFILE_RETIRED_ARTIFACT_INVALID")
    if len(artifacts) != len(set(artifacts)):
        raise ValueError("EXECUTION_PROFILE_RETIRED_ARTIFACT_DUPLICATE")
    patterns = retired.get("text_patterns")
    if not isinstance(patterns, list) or any(
        not isinstance(item, str) or not item for item in patterns
    ):
        raise ValueError("EXECUTION_PROFILE_RETIRED_PATTERN_INVALID")
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError("EXECUTION_PROFILE_RETIRED_PATTERN_INVALID") from exc
    removals = retired.get("remove_exact_text")
    if not isinstance(removals, list) or any(
        not isinstance(item, str) or not item for item in removals
    ):
        raise ValueError("EXECUTION_PROFILE_RETIRED_REMOVAL_INVALID")

    effect_policy = raw.get("effect_policy")
    if not isinstance(effect_policy, dict) or set(effect_policy) != {
        "trusted_boundary_required"
    }:
        raise ValueError("EXECUTION_PROFILE_EFFECT_POLICY_INVALID")
    high_risk_effects = _profile_string_list(
        effect_policy.get("trusted_boundary_required"),
        "EXECUTION_PROFILE_EFFECTS",
        EFFECT_ID_RE,
    )
    missing_high_risk = sorted(
        MANDATORY_TRUSTED_BOUNDARY_EFFECTS.difference(high_risk_effects)
    )
    if missing_high_risk:
        raise ValueError(
            "EXECUTION_PROFILE_MANDATORY_EFFECT_MISSING:" + missing_high_risk[0]
        )

    migration = raw.get("policy_migration")
    if not isinstance(migration, dict) or set(migration) != {
        "legacy_execution_profile_markers",
        "legacy_external_write_markers",
    }:
        raise ValueError("EXECUTION_PROFILE_POLICY_MIGRATION_INVALID")
    for key in (
        "legacy_execution_profile_markers",
        "legacy_external_write_markers",
    ):
        values = migration.get(key)
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item for item in values
        ):
            raise ValueError("EXECUTION_PROFILE_POLICY_MIGRATION_INVALID")

    return {
        "profile_id": profile_id,
        "revision": 1,
        "runtime": {
            "primary": primary,
            "optional_reviewers": reviewers,
            "disabled": disabled,
        },
    }


def _profile_aware_floor_source_reasons(
    content: str, profile: dict[str, object]
) -> list[str]:
    reasons: list[str] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return ["EXECUTION_PROFILE_BOOTSTRAP_FLOOR_INVALID_PYTHON"]

    imported: set[str] = set()
    defined: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "execution_profile":
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)

    required_imports = {
        "load_profile_text",
        "profile_transition_reasons",
        "retired_artifact_paths",
        "retired_rule_present",
    }
    for name in sorted(required_imports - imported):
        reasons.append(
            "EXECUTION_PROFILE_BOOTSTRAP_FLOOR_IMPORT_MISSING:" + name
        )
    for name in ("evaluate", "_execution_surface_reasons"):
        if name not in defined:
            reasons.append(
                "EXECUTION_PROFILE_BOOTSTRAP_FLOOR_FUNCTION_MISSING:" + name
            )
    for name in sorted(required_imports - called):
        reasons.append(
            "EXECUTION_PROFILE_BOOTSTRAP_FLOOR_CALL_MISSING:" + name
        )

    runtime = profile.get("runtime") or {}
    if isinstance(runtime, dict):
        names = {
            str(runtime.get("primary") or ""),
            *[str(item) for item in runtime.get("optional_reviewers", ())],
            *[str(item) for item in runtime.get("disabled", ())],
        }
        if any(name and name in content for name in names):
            reasons.append("PROVIDER_RUNTIME_COUPLING:tools/governance_floor.py")
    return reasons


def _execution_profile_bootstrap_reasons(
    root: Path, head: str, profile: dict[str, object]
) -> list[str]:
    reasons: list[str] = []
    agents = _read_at(root, head, "AGENTS.md")
    if agents is None:
        reasons.append("MANAGED_GOVERNANCE_PATH_MISSING:AGENTS.md")
    else:
        for token in (
            "Execution profile authority:",
            EXECUTION_PROFILE_PATH,
            "Execution authority precedence:",
        ):
            if token not in agents:
                reasons.append(
                    f"MANAGED_EXECUTION_INVARIANT_MISSING:AGENTS.md:{token}"
                )
        if RETIRED_AGENTS_RE.search(agents):
            reasons.append("RETIRED_IMPLEMENTER_REINTRODUCED:AGENTS.md")

    context = _read_at(root, head, "tools/context_epoch.py")
    if context is None:
        reasons.append("MANAGED_GOVERNANCE_PATH_MISSING:tools/context_epoch.py")
    else:
        for token in ("load_profile", "packet_authority", "EXECUTION_PROFILE_REVISION"):
            if token not in context:
                reasons.append(
                    "MANAGED_EXECUTION_INVARIANT_MISSING:"
                    f"tools/context_epoch.py:{token}"
                )
        runtime = profile.get("runtime") or {}
        if isinstance(runtime, dict):
            names = {
                str(runtime.get("primary") or ""),
                *[str(item) for item in runtime.get("optional_reviewers", ())],
                *[str(item) for item in runtime.get("disabled", ())],
            }
            if any(name and name in context for name in names):
                reasons.append("PROVIDER_RUNTIME_COUPLING:tools/context_epoch.py")
        if "IMPLEMENTER_INVALID" in context:
            reasons.append("LEGACY_IMPLEMENTER_GUARD_RETAINED:tools/context_epoch.py")

    floor_source = _read_at(root, head, GOVERNANCE_HELPER)
    if floor_source is None:
        reasons.append(f"MANAGED_GOVERNANCE_PATH_MISSING:{GOVERNANCE_HELPER}")
    else:
        reasons.extend(_profile_aware_floor_source_reasons(floor_source, profile))

    helper = _read_at(root, head, EXECUTION_PROFILE_HELPER_PATH)
    if helper is None:
        reasons.append(
            f"MANAGED_GOVERNANCE_PATH_MISSING:{EXECUTION_PROFILE_HELPER_PATH}"
        )
    else:
        for token in (
            "PROFILE_PATH",
            "load_profile_text",
            "packet_authority",
            "profile_transition_reasons",
            "requires_trusted_boundary",
        ):
            if token not in helper:
                reasons.append(
                    f"EXECUTION_PROFILE_HELPER_INVARIANT_MISSING:{token}"
                )

    schema_text = _read_at(root, head, EXECUTION_PROFILE_SCHEMA_PATH)
    if schema_text is None:
        reasons.append(
            f"MANAGED_GOVERNANCE_PATH_MISSING:{EXECUTION_PROFILE_SCHEMA_PATH}"
        )
    else:
        try:
            schema = json.loads(schema_text)
        except json.JSONDecodeError:
            reasons.append("EXECUTION_PROFILE_SCHEMA_INVALID_JSON")
        else:
            required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
            expected = {
                "contract_version",
                "profile_id",
                "revision",
                "runtime",
                "packet_compatibility",
                "retired_surface",
                "effect_policy",
                "policy_migration",
            }
            if not isinstance(schema, dict) or schema.get("additionalProperties") is not False:
                reasons.append("EXECUTION_PROFILE_SCHEMA_NOT_FAIL_CLOSED")
            if required != expected:
                reasons.append("EXECUTION_PROFILE_SCHEMA_REQUIRED_SET_INVALID")
    return reasons


def _root_migration_reasons(
    root: Path,
    base: str,
    head: str,
    base_epoch: int,
    head_epoch: int,
    changed_paths: list[str],
) -> list[str]:
    if not changed_paths:
        return []
    reasons: list[str] = []
    text = _read_at(root, head, ROOT_MIGRATION_MANIFEST)
    if text is None:
        return ["GOVERNANCE_ROOT_MIGRATION_MANIFEST_MISSING"]
    try:
        payload = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return ["GOVERNANCE_ROOT_MIGRATION_MANIFEST_INVALID"]
    if not isinstance(payload, dict):
        return ["GOVERNANCE_ROOT_MIGRATION_MANIFEST_INVALID"]
    if payload.get("contract_version") != 1:
        reasons.append("GOVERNANCE_ROOT_MIGRATION_VERSION_INVALID")
    if payload.get("base_sha") != base:
        reasons.append("GOVERNANCE_ROOT_MIGRATION_BASE_MISMATCH")
    if payload.get("from_policy_epoch") != base_epoch:
        reasons.append("GOVERNANCE_ROOT_MIGRATION_FROM_EPOCH_MISMATCH")
    if payload.get("to_policy_epoch") != head_epoch or head_epoch != base_epoch + 1:
        reasons.append("GOVERNANCE_ROOT_MIGRATION_TO_EPOCH_INVALID")
    if payload.get("requires_exact_head_validate") is not True:
        reasons.append("GOVERNANCE_ROOT_MIGRATION_VALIDATE_REQUIRED")
    if payload.get("automation_eligible") is not False:
        reasons.append("GOVERNANCE_ROOT_MIGRATION_AUTOMATION_MUST_BE_FALSE")
    rationale = payload.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 1000:
        reasons.append("GOVERNANCE_ROOT_MIGRATION_RATIONALE_INVALID")

    entries = payload.get("changed_surfaces")
    observed: dict[str, str] = {}
    if not isinstance(entries, list):
        reasons.append("GOVERNANCE_ROOT_MIGRATION_SURFACES_INVALID")
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            reasons.append("GOVERNANCE_ROOT_MIGRATION_SURFACES_INVALID")
            continue
        path = entry.get("path")
        blob = entry.get("head_blob_sha")
        if not isinstance(path, str) or path in observed:
            reasons.append("GOVERNANCE_ROOT_MIGRATION_SURFACES_INVALID")
            continue
        if not isinstance(blob, str) or FULL_SHA_RE.fullmatch(blob) is None:
            reasons.append(f"GOVERNANCE_ROOT_MIGRATION_BLOB_INVALID:{path}")
            continue
        observed[path] = blob
    expected = set(changed_paths)
    if set(observed) != expected:
        reasons.append("GOVERNANCE_ROOT_MIGRATION_SURFACE_SET_MISMATCH")
    for path in sorted(expected & set(observed)):
        if _blob_sha(root, head, path) != observed[path]:
            reasons.append(f"GOVERNANCE_ROOT_MIGRATION_BLOB_MISMATCH:{path}")
    return reasons


def _parse_workflow(text: str | None, missing_path: str) -> tuple[dict[str, object] | None, list[str]]:
    if text is None:
        return None, [f"MANAGED_GOVERNANCE_PATH_MISSING:{missing_path}"]
    try:
        payload = yaml.load(text, Loader=yaml.BaseLoader) or {}
    except yaml.YAMLError:
        return None, [f"GOVERNANCE_WORKFLOW_INVALID_YAML:{missing_path}"]
    if not isinstance(payload, dict):
        return None, [f"GOVERNANCE_WORKFLOW_INVALID_ROOT:{missing_path}"]
    return payload, []


def _event_unfiltered(triggers: object, event: str) -> bool:
    if not isinstance(triggers, dict) or event not in triggers:
        return False
    return triggers.get(event) in (None, "", {})


def _profile_engineering(profile: dict[str, object]) -> dict[str, object]:
    engineering = profile.get("engineering_system") or {}
    return engineering if isinstance(engineering, dict) else {}


def _baseline(profile: dict[str, object]) -> str:
    return str(_profile_engineering(profile).get("baseline") or "").strip()


def _uses_pin_reasons(
    job: object,
    *,
    job_name: str,
    prefix: str,
    baseline: str,
    condition: str,
) -> list[str]:
    if not isinstance(job, dict):
        return [f"GOVERNANCE_MANAGED_JOB_MISSING:{job_name}"]
    reasons: list[str] = []
    if str(job.get("if") or "").strip() != condition:
        reasons.append(f"GOVERNANCE_MANAGED_JOB_CONDITION_INVALID:{job_name}")
    uses = str(job.get("uses") or "").strip()
    if not uses.startswith(prefix):
        reasons.append(f"GOVERNANCE_MANAGED_JOB_USES_INVALID:{job_name}")
        return reasons
    pinned = uses.removeprefix(prefix)
    if FULL_SHA_RE.fullmatch(pinned) is None:
        reasons.append(f"GOVERNANCE_MANAGED_JOB_PIN_INVALID:{job_name}")
    elif baseline and pinned != baseline:
        reasons.append(f"GOVERNANCE_MANAGED_JOB_BASELINE_MISMATCH:{job_name}")
    return reasons


def _adopted_workflow_reasons(
    text: str | None, profile: dict[str, object]
) -> list[str]:
    payload, reasons = _parse_workflow(text, ADOPTED_ENGINEERING_WORKFLOW)
    if payload is None:
        return reasons
    triggers = payload.get("on")
    if not isinstance(triggers, dict) or "pull_request_target" not in triggers:
        reasons.append("GOVERNANCE_WORKFLOW_TRIGGER_MISSING:pull_request_target")
    elif not _event_unfiltered(triggers, "pull_request_target"):
        reasons.append("GOVERNANCE_WORKFLOW_TRIGGER_FILTERED:pull_request_target")
    if not isinstance(triggers, dict) or "pull_request" not in triggers:
        reasons.append("GOVERNANCE_WORKFLOW_TRIGGER_MISSING:pull_request")
    elif not _event_unfiltered(triggers, "pull_request"):
        reasons.append("GOVERNANCE_WORKFLOW_TRIGGER_FILTERED:pull_request")

    permissions = payload.get("permissions")
    if permissions != {"contents": "read"}:
        reasons.append("GOVERNANCE_WORKFLOW_PERMISSIONS_INVALID")

    jobs = payload.get("jobs")
    if not isinstance(jobs, dict):
        reasons.append("GOVERNANCE_WORKFLOW_JOB_MISSING")
        return reasons

    baseline = _baseline(profile)
    if FULL_SHA_RE.fullmatch(baseline) is None:
        reasons.append("GOVERNANCE_WORKFLOW_BASELINE_INVALID")

    floor_job = jobs.get("governance-floor")
    reasons.extend(
        _uses_pin_reasons(
            floor_job,
            job_name="governance-floor",
            prefix=GOVERNANCE_WORKFLOW_PREFIX,
            baseline=baseline,
            condition=EXPECTED_FLOOR_CONDITION,
        )
    )
    if isinstance(floor_job, dict):
        inputs = floor_job.get("with")
        if not isinstance(inputs, dict):
            reasons.append("GOVERNANCE_WORKFLOW_INPUTS_INVALID:governance-floor")
        else:
            if str(inputs.get("base_sha") or "").strip() != EXPECTED_BASE_INPUT:
                reasons.append("GOVERNANCE_WORKFLOW_BASE_INPUT_INVALID")
            if str(inputs.get("head_sha") or "").strip() != EXPECTED_HEAD_INPUT:
                reasons.append("GOVERNANCE_WORKFLOW_HEAD_INPUT_INVALID")

    reasons.extend(
        _uses_pin_reasons(
            jobs.get("adoption-compliance"),
            job_name="adoption-compliance",
            prefix=ADOPTION_WORKFLOW_PREFIX,
            baseline=baseline,
            condition=EXPECTED_PR_CONDITION,
        )
    )
    reasons.extend(
        _uses_pin_reasons(
            jobs.get("enforcement-reconcile"),
            job_name="enforcement-reconcile",
            prefix=ENFORCEMENT_WORKFLOW_PREFIX,
            baseline=baseline,
            condition=EXPECTED_PR_CONDITION,
        )
    )

    ci_mode = str(_profile_engineering(profile).get("ci_mode") or "")
    expected_jobs = {"governance-floor", "adoption-compliance", "enforcement-reconcile"}
    if ci_mode == "shared":
        expected_jobs.add("affected-tests")
        affected = jobs.get("affected-tests")
        reasons.extend(
            _uses_pin_reasons(
                affected,
                job_name="affected-tests",
                prefix=AFFECTED_WORKFLOW_PREFIX,
                baseline=baseline,
                condition=EXPECTED_PR_CONDITION,
            )
        )
        if isinstance(affected, dict):
            inputs = affected.get("with")
            if not isinstance(inputs, dict):
                reasons.append("GOVERNANCE_WORKFLOW_INPUTS_INVALID:affected-tests")
            else:
                if str(inputs.get("manifest_path") or "").strip() != ".engineering/tests.yaml":
                    reasons.append("GOVERNANCE_AFFECTED_MANIFEST_INVALID")
                if str(inputs.get("trigger") or "").strip() != "pr":
                    reasons.append("GOVERNANCE_AFFECTED_TRIGGER_INVALID")
    elif ci_mode == "native":
        if "affected-tests" in jobs:
            reasons.append("GOVERNANCE_NATIVE_DUPLICATE_AFFECTED_TESTS")
    else:
        reasons.append("GOVERNANCE_CI_MODE_INVALID")

    if set(jobs) != expected_jobs:
        reasons.append("GOVERNANCE_MANAGED_JOB_SET_INVALID")
    return reasons


def _canonical_floor_workflow_reasons(text: str | None) -> list[str]:
    payload, reasons = _parse_workflow(text, GOVERNANCE_REUSABLE_WORKFLOW)
    if payload is None:
        return reasons
    triggers = payload.get("on")
    if not isinstance(triggers, dict) or "workflow_call" not in triggers:
        reasons.append("GOVERNANCE_CANONICAL_WORKFLOW_CALL_MISSING")
    if not isinstance(triggers, dict) or "pull_request_target" not in triggers:
        reasons.append("GOVERNANCE_CANONICAL_TRIGGER_MISSING")
    elif not _event_unfiltered(triggers, "pull_request_target"):
        reasons.append("GOVERNANCE_CANONICAL_TRIGGER_FILTERED")

    permissions = payload.get("permissions")
    if not isinstance(permissions, dict) or str(permissions.get("contents") or "") != "read":
        reasons.append("GOVERNANCE_CANONICAL_PERMISSIONS_INVALID")

    jobs = payload.get("jobs")
    job = jobs.get("governance-floor") if isinstance(jobs, dict) else None
    if not isinstance(job, dict):
        reasons.append("GOVERNANCE_CANONICAL_JOB_MISSING")
        return reasons
    if not str(job.get("runs-on") or "").strip():
        reasons.append("GOVERNANCE_CANONICAL_RUNNER_MISSING")
    steps = job.get("steps")
    if not isinstance(steps, list):
        reasons.append("GOVERNANCE_CANONICAL_STEPS_MISSING")
        return reasons

    checkout = next(
        (
            step
            for step in steps
            if isinstance(step, dict)
            and str(step.get("uses") or "").startswith("actions/checkout@")
        ),
        None,
    )
    if not isinstance(checkout, dict):
        reasons.append("GOVERNANCE_CANONICAL_CHECKOUT_MISSING")
    else:
        uses = str(checkout.get("uses") or "")
        pin = uses.rsplit("@", 1)[-1]
        if FULL_SHA_RE.fullmatch(pin) is None:
            reasons.append("GOVERNANCE_CANONICAL_CHECKOUT_PIN_INVALID")
        checkout_with = checkout.get("with")
        if not isinstance(checkout_with, dict):
            reasons.append("GOVERNANCE_CANONICAL_CHECKOUT_INPUTS_INVALID")
        else:
            if str(checkout_with.get("ref") or "").strip() != EXPECTED_CANONICAL_BASE_REF:
                reasons.append("GOVERNANCE_CANONICAL_CHECKOUT_REF_INVALID")
            if str(checkout_with.get("fetch-depth") or "").strip() != "0":
                reasons.append("GOVERNANCE_CANONICAL_CHECKOUT_DEPTH_INVALID")

    fetch_step = next(
        (
            step
            for step in steps
            if isinstance(step, dict)
            and str(step.get("name") or "") == "Fetch candidate commit as data only"
        ),
        None,
    )
    if not isinstance(fetch_step, dict):
        reasons.append("GOVERNANCE_CANONICAL_FETCH_STEP_MISSING")
    else:
        fetch_env = fetch_step.get("env")
        if (
            not isinstance(fetch_env, dict)
            or str(fetch_env.get("HEAD_SHA") or "").strip()
            != EXPECTED_CANONICAL_HEAD_REF
        ):
            reasons.append("GOVERNANCE_CANONICAL_FETCH_HEAD_INVALID")

    enforce_step = next(
        (
            step
            for step in steps
            if isinstance(step, dict)
            and str(step.get("name") or "") == "Enforce base-branch governance floor"
        ),
        None,
    )
    if not isinstance(enforce_step, dict):
        reasons.append("GOVERNANCE_CANONICAL_ENFORCE_STEP_MISSING")
    else:
        enforce_env = enforce_step.get("env")
        if not isinstance(enforce_env, dict):
            reasons.append("GOVERNANCE_CANONICAL_ENFORCE_ENV_INVALID")
        else:
            if str(enforce_env.get("BASE_SHA") or "").strip() != EXPECTED_CANONICAL_BASE_REF:
                reasons.append("GOVERNANCE_CANONICAL_ENFORCE_BASE_INVALID")
            if str(enforce_env.get("HEAD_SHA") or "").strip() != EXPECTED_CANONICAL_HEAD_REF:
                reasons.append("GOVERNANCE_CANONICAL_ENFORCE_HEAD_INVALID")

    run_text = "\n".join(
        str(step.get("run") or "")
        for step in steps
        if isinstance(step, dict)
    )
    for token, reason in (
        (".engineering/requirements-engineering-system.txt", "GOVERNANCE_CANONICAL_DEPENDENCIES_INVALID"),
        ('git fetch --no-tags --depth=1 origin "$HEAD_SHA"', "GOVERNANCE_CANONICAL_FETCH_INVALID"),
        ("python3 tools/governance_floor.py check", "GOVERNANCE_CANONICAL_HELPER_INVOCATION_INVALID"),
        ('--base-ref "$BASE_SHA"', "GOVERNANCE_CANONICAL_BASE_REF_INVALID"),
        ('--head-ref "$HEAD_SHA"', "GOVERNANCE_CANONICAL_HEAD_REF_INVALID"),
    ):
        if token not in run_text:
            reasons.append(reason)
    return reasons


def _canonical_validate_reasons(text: str | None) -> list[str]:
    payload, reasons = _parse_workflow(text, CANONICAL_VALIDATE_WORKFLOW)
    if payload is None:
        return reasons
    triggers = payload.get("on")
    if not isinstance(triggers, dict) or "pull_request" not in triggers:
        reasons.append("GOVERNANCE_CANONICAL_VALIDATE_PR_MISSING")
    elif not _event_unfiltered(triggers, "pull_request"):
        reasons.append("GOVERNANCE_CANONICAL_VALIDATE_PR_FILTERED")
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict) or not isinstance(jobs.get("validate"), dict):
        reasons.append("GOVERNANCE_CANONICAL_VALIDATE_JOB_MISSING")
    return reasons


def _execution_surface_reasons(path: str, content: str) -> list[str]:
    reasons: list[str] = []
    if path == "AGENTS.md":
        if RETIRED_AGENTS_RE.search(content):
            reasons.append("RETIRED_IMPLEMENTER_REINTRODUCED:AGENTS.md")
        for required in (
            "Execution authority precedence:",
            "IMPLEMENTER=CHATGPT_CHAT",
        ):
            if required not in content:
                reasons.append(f"MANAGED_EXECUTION_INVARIANT_MISSING:AGENTS.md:{required}")
    elif path == "tools/context_epoch.py":
        if '"CURSOR"' in content or "'CURSOR'" in content:
            reasons.append("RETIRED_IMPLEMENTER_REINTRODUCED:tools/context_epoch.py")
        required = (
            'if implementer and implementer != "CHATGPT_CHAT":',
            'blocking.append("IMPLEMENTER_INVALID")',
        )
        for token in required:
            if token not in content:
                reasons.append(
                    f"MANAGED_EXECUTION_INVARIANT_MISSING:tools/context_epoch.py:{token}"
                )
    elif path == "tools/engineering-context.py":
        if ".cursor" in content.lower():
            reasons.append(
                "RETIRED_IMPLEMENTER_REINTRODUCED:tools/engineering-context.py"
            )
        for required in ("AGENTS.md", ".engineering/project.yaml"):
            if required not in content:
                reasons.append(
                    f"MANAGED_EXECUTION_INVARIANT_MISSING:tools/engineering-context.py:{required}"
                )
    return reasons



def evaluate(root: Path, base_ref: str, head_ref: str) -> tuple[str, list[str], int, int]:
    root = root.resolve()
    base = _commit(root, base_ref, "base")
    head = _commit(root, head_ref, "head")
    base_profile = _profile(_read_at(root, base, ".engineering/project.yaml"), "base")
    head_profile = _profile(_read_at(root, head, ".engineering/project.yaml"), "head")
    base_epoch = _policy_epoch(base_profile, "base")
    head_epoch = _policy_epoch(head_profile, "head")
    mode = str(_profile_engineering(head_profile).get("mode") or "")
    reasons: list[str] = []

    base_execution_profile_text = _read_at(root, base, EXECUTION_PROFILE_PATH)
    head_execution_profile_text = _read_at(root, head, EXECUTION_PROFILE_PATH)
    execution_profile_bootstrap = (
        base_execution_profile_text is None and head_execution_profile_text is not None
    )
    bootstrap_profile: dict[str, object] | None = None
    if base_execution_profile_text is not None and head_execution_profile_text is None:
        reasons.append("EXECUTION_PROFILE_HEAD_MISSING")
    elif execution_profile_bootstrap:
        try:
            bootstrap_profile = _bootstrap_execution_profile(
                head_execution_profile_text or ""
            )
        except ValueError as exc:
            reasons.append(str(exc))
        if _read_at(root, base, GOVERNANCE_HELPER) == _read_at(
            root, head, GOVERNANCE_HELPER
        ):
            reasons.append("EXECUTION_PROFILE_BOOTSTRAP_FLOOR_HELPER_UNCHANGED")

    if head_epoch < base_epoch:
        reasons.append(
            f"GOVERNANCE_POLICY_EPOCH_REGRESSION:base={base_epoch}:head={head_epoch}"
        )

    profile_surfaces = (
        EXECUTION_PROFILE_BOOTSTRAP_SURFACES if execution_profile_bootstrap else ()
    )
    canonical_cutover_surfaces = ()
    if mode == "canonical":
        canonical_cutover_surfaces = tuple(
            path
            for path in CANONICAL_EPOCH_GUARDED_GOVERNANCE_SURFACES
            if _read_at(root, base, path) is not None
            or _read_at(root, head, path) is not None
        )
    epoch_guarded_surfaces = (
        PROTECTED_GOVERNANCE_SURFACES
        + EPOCH_GUARDED_GOVERNANCE_SURFACES
        + profile_surfaces
        + canonical_cutover_surfaces
    )
    root_migration_surfaces = set(EPOCH_GUARDED_GOVERNANCE_SURFACES)
    root_migration_surfaces.update(profile_surfaces)
    root_migration_surfaces.update(canonical_cutover_surfaces)
    changed_root_surfaces: list[str] = []
    for path in epoch_guarded_surfaces:
        base_content = _read_at(root, base, path)
        head_content = _read_at(root, head, path)
        if head_content is None:
            reasons.append(f"MANAGED_GOVERNANCE_PATH_MISSING:{path}")
            continue
        if base_content != head_content:
            if path in root_migration_surfaces:
                changed_root_surfaces.append(path)
            if head_epoch == base_epoch:
                reasons.append(
                    f"GOVERNANCE_SURFACE_CHANGED_WITHOUT_POLICY_EPOCH:{path}"
                )

    if changed_root_surfaces and head_epoch > base_epoch:
        reasons.extend(
            _root_migration_reasons(
                root,
                base,
                head,
                base_epoch,
                head_epoch,
                changed_root_surfaces,
            )
        )

    if mode == "adopted":
        workflow = _read_at(root, head, ADOPTED_ENGINEERING_WORKFLOW)
        reasons.extend(_adopted_workflow_reasons(workflow, head_profile))
    elif mode == "canonical":
        floor_workflow = _read_at(root, head, GOVERNANCE_REUSABLE_WORKFLOW)
        reasons.extend(_canonical_floor_workflow_reasons(floor_workflow))
        validate_workflow = _read_at(root, head, CANONICAL_VALIDATE_WORKFLOW)
        reasons.extend(_canonical_validate_reasons(validate_workflow))
    else:
        reasons.append("GOVERNANCE_PROJECT_MODE_INVALID")

    if execution_profile_bootstrap:
        if bootstrap_profile is not None:
            reasons.extend(
                _execution_profile_bootstrap_reasons(root, head, bootstrap_profile)
            )
    else:
        for path in MANAGED_EXECUTION_SURFACES:
            content = _read_at(root, head, path)
            if content is None:
                reasons.append(f"MANAGED_GOVERNANCE_PATH_MISSING:{path}")
                continue
            reasons.extend(_execution_surface_reasons(path, content))

    for path in RETIRED_AGENT_ARTIFACTS:
        if _tree_has_path(root, head, path):
            reasons.append(f"RETIRED_AGENT_ARTIFACT_REINTRODUCED:{path}")

    reasons = sorted(set(reasons))
    return ("BLOCK" if reasons else "PASS"), reasons, base_epoch, head_epoch


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--root", default=".")
    check.add_argument("--base-ref", required=True)
    check.add_argument("--head-ref", required=True)
    args = parser.parse_args()

    try:
        status, reasons, base_epoch, head_epoch = evaluate(
            Path(args.root), args.base_ref, args.head_ref
        )
    except ValueError as exc:
        print("GOVERNANCE_FLOOR=BLOCK")
        print(f"REASON={exc}")
        return 2

    print(f"GOVERNANCE_FLOOR_BASE_EPOCH={base_epoch}")
    print(f"GOVERNANCE_FLOOR_HEAD_EPOCH={head_epoch}")
    for reason in reasons:
        print(f"REASON={reason}")
    print(f"GOVERNANCE_FLOOR={status}")
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
