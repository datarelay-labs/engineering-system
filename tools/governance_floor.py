#!/usr/bin/env python3
"""Base-owned fail-closed governance floor for Engineering System pull requests."""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

import yaml

try:
    from execution_profile import (
        ProfileError,
        load_profile_text,
        profile_transition_reasons,
        retired_artifact_paths,
        retired_rule_present,
    )
    PROFILE_HELPER_FALLBACK_ACTIVE = False
except ModuleNotFoundError as exc:
    if exc.name != "execution_profile":
        raise

    PROFILE_HELPER_FALLBACK_ACTIVE = True
    _PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    _RUNTIME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    _EFFECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

    class ProfileError(ValueError):
        pass

    def _fallback_mapping(value: object, label: str) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ProfileError(f"{label}_INVALID")
        return value

    def _fallback_string_list(
        value: object, label: str, pattern: re.Pattern[str]
    ) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise ProfileError(f"{label}_INVALID")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or pattern.fullmatch(item) is None:
                raise ProfileError(f"{label}_INVALID")
            if item in out:
                raise ProfileError(f"{label}_DUPLICATE")
            out.append(item)
        return tuple(out)

    def load_profile_text(text: str) -> dict[str, object]:
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError as yaml_exc:
            raise ProfileError("PROFILE_YAML_INVALID") from yaml_exc
        data = _fallback_mapping(raw, "PROFILE")
        allowed = {
            "contract_version",
            "profile_id",
            "revision",
            "authority_contract",
            "runtime",
            "packet_compatibility",
            "retired_surface",
            "effect_policy",
            "policy_migration",
        }
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ProfileError("PROFILE_UNKNOWN_KEY:" + unknown[0])
        if data.get("contract_version") != 1:
            raise ProfileError("PROFILE_CONTRACT_VERSION_INVALID")

        profile_id = data.get("profile_id")
        if (
            not isinstance(profile_id, str)
            or _PROFILE_ID_RE.fullmatch(profile_id) is None
        ):
            raise ProfileError("PROFILE_ID_INVALID")
        revision = data.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ProfileError("PROFILE_REVISION_INVALID")
        authority_contract = data.get("authority_contract")
        if authority_contract not in {"legacy-v2", "profile-v3"}:
            raise ProfileError("PROFILE_AUTHORITY_CONTRACT_INVALID")

        runtime = _fallback_mapping(data.get("runtime"), "PROFILE_RUNTIME")
        if set(runtime) != {"primary", "optional_reviewers", "disabled"}:
            raise ProfileError("PROFILE_RUNTIME_KEYS_INVALID")
        primary = runtime.get("primary")
        if (
            not isinstance(primary, str)
            or _RUNTIME_ID_RE.fullmatch(primary) is None
        ):
            raise ProfileError("PROFILE_PRIMARY_RUNTIME_INVALID")
        reviewers = _fallback_string_list(
            runtime.get("optional_reviewers"),
            "PROFILE_OPTIONAL_REVIEWERS",
            _RUNTIME_ID_RE,
        )
        disabled = _fallback_string_list(
            runtime.get("disabled"),
            "PROFILE_DISABLED_RUNTIMES",
            _RUNTIME_ID_RE,
        )
        if primary in disabled:
            raise ProfileError("PROFILE_PRIMARY_RUNTIME_DISABLED")

        compatibility = _fallback_mapping(
            data.get("packet_compatibility"),
            "PROFILE_PACKET_COMPATIBILITY",
        )
        if set(compatibility) != {"legacy_v2_implementers"}:
            raise ProfileError("PROFILE_PACKET_COMPATIBILITY_KEYS_INVALID")
        legacy = compatibility.get("legacy_v2_implementers")
        if not isinstance(legacy, dict):
            raise ProfileError("PROFILE_LEGACY_IMPLEMENTERS_INVALID")
        normalized_legacy: dict[str, str] = {}
        for key, target in legacy.items():
            if (
                not isinstance(key, str)
                or _RUNTIME_ID_RE.fullmatch(key) is None
                or not isinstance(target, str)
                or _PROFILE_ID_RE.fullmatch(target) is None
            ):
                raise ProfileError("PROFILE_LEGACY_IMPLEMENTERS_INVALID")
            if target != profile_id:
                raise ProfileError("PROFILE_LEGACY_IMPLEMENTER_TARGET_INVALID")
            if key in disabled:
                raise ProfileError("PROFILE_LEGACY_IMPLEMENTER_DISABLED")
            normalized_legacy[key] = target

        retired = _fallback_mapping(
            data.get("retired_surface"), "PROFILE_RETIRED_SURFACE"
        )
        if set(retired) != {
            "artifact_paths",
            "text_patterns",
            "remove_exact_text",
        }:
            raise ProfileError("PROFILE_RETIRED_SURFACE_KEYS_INVALID")
        artifacts = retired.get("artifact_paths")
        patterns = retired.get("text_patterns")
        removals = retired.get("remove_exact_text")
        if (
            not isinstance(artifacts, list)
            or any(
                not isinstance(item, str)
                or not item
                or Path(item).is_absolute()
                or ".." in Path(item).parts
                for item in artifacts
            )
        ):
            raise ProfileError("PROFILE_RETIRED_ARTIFACTS_INVALID")
        if len(artifacts) != len(set(artifacts)):
            raise ProfileError("PROFILE_RETIRED_ARTIFACTS_DUPLICATE")
        if (
            not isinstance(patterns, list)
            or any(not isinstance(item, str) or not item for item in patterns)
        ):
            raise ProfileError("PROFILE_RETIRED_PATTERNS_INVALID")
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as regex_exc:
                raise ProfileError("PROFILE_RETIRED_PATTERN_INVALID") from regex_exc
        if (
            not isinstance(removals, list)
            or any(not isinstance(item, str) or not item for item in removals)
        ):
            raise ProfileError("PROFILE_RETIRED_REMOVALS_INVALID")

        migration = _fallback_mapping(
            data.get("policy_migration"), "PROFILE_POLICY_MIGRATION"
        )
        if set(migration) != {
            "legacy_execution_profile_markers",
            "legacy_external_write_markers",
        }:
            raise ProfileError("PROFILE_POLICY_MIGRATION_KEYS_INVALID")
        legacy_profile_markers = migration.get(
            "legacy_execution_profile_markers"
        )
        legacy_write_markers = migration.get("legacy_external_write_markers")
        if (
            not isinstance(legacy_profile_markers, list)
            or any(
                not isinstance(item, str) or not item
                for item in legacy_profile_markers
            )
        ):
            raise ProfileError("PROFILE_LEGACY_EXECUTION_MARKERS_INVALID")
        if (
            not isinstance(legacy_write_markers, list)
            or any(
                not isinstance(item, str) or not item
                for item in legacy_write_markers
            )
        ):
            raise ProfileError("PROFILE_LEGACY_WRITE_MARKERS_INVALID")

        effect_policy = _fallback_mapping(
            data.get("effect_policy"), "PROFILE_EFFECT_POLICY"
        )
        if set(effect_policy) != {"trusted_boundary_required"}:
            raise ProfileError("PROFILE_EFFECT_POLICY_KEYS_INVALID")
        high_risk = _fallback_string_list(
            effect_policy.get("trusted_boundary_required"),
            "PROFILE_TRUSTED_BOUNDARY_EFFECTS",
            _EFFECT_ID_RE,
        )
        return {
            "contract_version": 1,
            "profile_id": profile_id,
            "revision": revision,
            "authority_contract": authority_contract,
            "runtime": {
                "primary": primary,
                "optional_reviewers": reviewers,
                "disabled": disabled,
            },
            "packet_compatibility": {
                "legacy_v2_implementers": normalized_legacy
            },
            "retired_surface": {
                "artifact_paths": tuple(artifacts),
                "text_patterns": tuple(patterns),
                "remove_exact_text": tuple(removals),
            },
            "effect_policy": {"trusted_boundary_required": high_risk},
            "policy_migration": {
                "legacy_execution_profile_markers": tuple(
                    legacy_profile_markers
                ),
                "legacy_external_write_markers": tuple(
                    legacy_write_markers
                ),
            },
        }

    def profile_transition_reasons(
        base_text: str, head_text: str
    ) -> list[str]:
        try:
            base = load_profile_text(base_text)
            head = load_profile_text(head_text)
        except ProfileError as profile_exc:
            return [str(profile_exc)]
        if base_text == head_text:
            return []
        if head["revision"] != base["revision"] + 1:
            return ["EXECUTION_PROFILE_REVISION_NOT_INCREMENTED"]
        return []

    def retired_artifact_paths(
        profile: dict[str, object]
    ) -> tuple[str, ...]:
        retired = profile.get("retired_surface") or {}
        if not isinstance(retired, dict):
            return ()
        return tuple(str(item) for item in retired.get("artifact_paths", ()))

    def retired_rule_present(
        text: str, profile: dict[str, object]
    ) -> bool:
        retired = profile.get("retired_surface") or {}
        if not isinstance(retired, dict):
            return False
        return any(
            re.search(str(pattern), text) is not None
            for pattern in retired.get("text_patterns", ())
        )

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MANAGED_EXECUTION_SURFACES = (
    "AGENTS.md",
    "tools/context_epoch.py",
    "tools/engineering-context.py",
)
PROTECTED_GOVERNANCE_SURFACES = (
    # Read-only orientation remains protected by policy epoch, but does not
    # itself define provider/runtime selection.
    "tools/engineering-context.py",
)
GOVERNANCE_HELPER = "tools/governance_floor.py"
GOVERNANCE_REUSABLE_WORKFLOW = ".github/workflows/governance-floor.yml"
GOVERNANCE_DEPENDENCY_MANIFEST = ".engineering/requirements-engineering-system.txt"
ROOT_MIGRATION_MANIFEST = ".engineering/governance-migration.yaml"
EXECUTION_PROFILE_SURFACES = (
    ".engineering/execution-profile.yaml",
    "tools/execution_profile.py",
    "schemas/execution-profile.schema.json",
)
EPOCH_GUARDED_GOVERNANCE_SURFACES = (
    GOVERNANCE_HELPER,
    GOVERNANCE_DEPENDENCY_MANIFEST,
    "tools/context_epoch.py",
    *EXECUTION_PROFILE_SURFACES,
)
POST_BRIDGE_CANONICAL_SURFACES = (
    ".github/workflows/adoption-compliance.yml",
    "tools/check-adoption.py",
    "tools/adopt.py",
    "tools/upgrade-adoption.py",
)
CANONICAL_EPOCH_GUARDED_GOVERNANCE_SURFACES = (
    GOVERNANCE_REUSABLE_WORKFLOW,
    *POST_BRIDGE_CANONICAL_SURFACES,
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


def _has_durable_stage_a_bridge(
    root: Path, base: str, expected_profile_id: str
) -> bool:
    """Prove the base descends from a governance-valid legacy-v2 bridge.

    A policy epoch number is not durable bridge evidence by itself: a pre-bridge
    branch can copy that number. For direct profile-v3 recovery after the managed
    profile bundle is lost, require an ancestor that actually carried the legacy-v2
    profile and whose own root migration still validates against its recorded base.
    """
    history = _git(
        root,
        "rev-list",
        "--max-count=128",
        base,
        "--",
        EXECUTION_PROFILE_SURFACES[0],
    )
    if history.returncode != 0:
        return False
    for candidate in history.stdout.splitlines():
        if FULL_SHA_RE.fullmatch(candidate) is None:
            continue
        profile_text = _read_at(root, candidate, EXECUTION_PROFILE_SURFACES[0])
        if profile_text is None:
            continue
        try:
            profile = load_profile_text(profile_text)
        except ProfileError:
            continue
        if (
            profile.get("authority_contract") != "legacy-v2"
            or profile.get("profile_id") != expected_profile_id
        ):
            continue

        manifest_text = _read_at(root, candidate, ROOT_MIGRATION_MANIFEST)
        if manifest_text is None:
            continue
        try:
            manifest = yaml.safe_load(manifest_text) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(manifest, dict):
            continue
        stage_base = manifest.get("base_sha")
        if not isinstance(stage_base, str) or FULL_SHA_RE.fullmatch(stage_base) is None:
            continue
        if _git(
            root, "merge-base", "--is-ancestor", stage_base, candidate
        ).returncode != 0:
            continue
        if _git(
            root, "merge-base", "--is-ancestor", candidate, base
        ).returncode != 0:
            continue
        try:
            status, _reasons, from_epoch, to_epoch = evaluate(
                root, stage_base, candidate
            )
        except ValueError:
            continue
        if status == "PASS" and from_epoch < 3 <= to_epoch:
            return True
    return False


def _has_durable_profile_v3_snapshot(
    root: Path, base: str, expected_profile_id: str
) -> bool:
    """Accept a committed post-bridge profile-v3 state already owned by the base.

    Repositories first adopted after the profile-v3 cutover legitimately have no
    legacy-v2 bridge in their own history. A prior committed profile-v3 snapshot at
    the post-bridge policy epoch is durable evidence because the current candidate
    cannot manufacture it in the immutable base ancestry.
    """
    history = _git(
        root,
        "rev-list",
        "--max-count=128",
        base,
        "--",
        EXECUTION_PROFILE_SURFACES[0],
    )
    if history.returncode != 0:
        return False
    for candidate in history.stdout.splitlines():
        if FULL_SHA_RE.fullmatch(candidate) is None:
            continue
        profile_text = _read_at(root, candidate, EXECUTION_PROFILE_SURFACES[0])
        if profile_text is None:
            continue
        try:
            profile = load_profile_text(profile_text)
            project = _profile(
                _read_at(root, candidate, ".engineering/project.yaml"),
                "post-bridge",
            )
            epoch = _policy_epoch(project, "post-bridge")
        except (ProfileError, ValueError):
            continue
        if (
            profile.get("authority_contract") != "profile-v3"
            or profile.get("profile_id") != expected_profile_id
            or epoch < 4
        ):
            continue
        engineering = project.get("engineering_system") or {}
        if not isinstance(engineering, dict):
            continue
        mode = str(engineering.get("mode") or "")
        if mode == "adopted":
            baseline = str(engineering.get("baseline") or "")
            if FULL_SHA_RE.fullmatch(baseline) is None:
                continue
        elif mode != "canonical":
            continue
        if any(
            _read_at(root, candidate, rel) is None
            for rel in EXECUTION_PROFILE_SURFACES
        ):
            continue
        execution_invalid = False
        for rel in MANAGED_EXECUTION_SURFACES:
            content = _read_at(root, candidate, rel)
            if content is None or _execution_surface_reasons(rel, content, profile):
                execution_invalid = True
                break
        if not execution_invalid:
            return True
    return False


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


def _execution_surface_reasons(path: str, content: str, profile: dict[str, object] | None = None) -> list[str]:
    reasons: list[str] = []
    if profile is None:
        try:
            profile = load_profile_text((Path(__file__).resolve().parents[1] / ".engineering/execution-profile.yaml").read_text(encoding="utf-8"))
        except (OSError, ProfileError) as exc:
            return [f"EXECUTION_PROFILE_FALLBACK_INVALID:{exc}"]
    contract = str(profile.get("authority_contract") or "")
    runtime = profile.get("runtime") or {}
    primary = str(runtime.get("primary") or "") if isinstance(runtime, dict) else ""
    disabled = [str(item) for item in (runtime.get("disabled") or [])] if isinstance(runtime, dict) else []
    reviewers = [str(item) for item in (runtime.get("optional_reviewers") or [])] if isinstance(runtime, dict) else []

    if path == "AGENTS.md":
        if retired_rule_present(content, profile):
            reasons.append("RETIRED_RUNTIME_REINTRODUCED:AGENTS.md")
        if "Execution authority precedence:" not in content:
            reasons.append("MANAGED_EXECUTION_INVARIANT_MISSING:AGENTS.md:Execution authority precedence:")
        if contract == "legacy-v2":
            marker = f"IMPLEMENTER={primary}"
            if not primary or marker not in content:
                reasons.append(f"MANAGED_EXECUTION_INVARIANT_MISSING:AGENTS.md:{marker}")
        elif contract == "profile-v3":
            for required in ("Execution profile authority:", ".engineering/execution-profile.yaml"):
                if required not in content:
                    reasons.append(f"MANAGED_EXECUTION_INVARIANT_MISSING:AGENTS.md:{required}")
        else:
            reasons.append("EXECUTION_PROFILE_AUTHORITY_CONTRACT_INVALID")
    elif path == "tools/context_epoch.py":
        if contract == "legacy-v2":
            if any(name and name in content for name in disabled):
                reasons.append("RETIRED_RUNTIME_REINTRODUCED:tools/context_epoch.py")
            required = (
                f'if implementer and implementer != "{primary}":',
                'blocking.append("IMPLEMENTER_INVALID")',
            )
            for token in required:
                if token not in content:
                    reasons.append(f"MANAGED_EXECUTION_INVARIANT_MISSING:tools/context_epoch.py:{token}")
        elif contract == "profile-v3":
            runtime_names = {primary, *disabled, *reviewers}
            if any(name and name in content for name in runtime_names) or "IMPLEMENTER_INVALID" in content:
                reasons.append("PROVIDER_RUNTIME_COUPLING:tools/context_epoch.py")
            for token in ("load_profile", "packet_authority", "EXECUTION_PROFILE_REVISION"):
                if token not in content:
                    reasons.append(f"MANAGED_EXECUTION_INVARIANT_MISSING:tools/context_epoch.py:{token}")
        else:
            reasons.append("EXECUTION_PROFILE_AUTHORITY_CONTRACT_INVALID")
    elif path == "tools/engineering-context.py":
        if retired_rule_present(content, profile):
            reasons.append("RETIRED_RUNTIME_REINTRODUCED:tools/engineering-context.py")
        for required in ("AGENTS.md", ".engineering/project.yaml"):
            if required not in content:
                reasons.append(f"MANAGED_EXECUTION_INVARIANT_MISSING:tools/engineering-context.py:{required}")
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

    base_execution_profile_text = _read_at(root, base, ".engineering/execution-profile.yaml")
    head_execution_profile_text = _read_at(root, head, ".engineering/execution-profile.yaml")
    base_execution_profile = None
    head_execution_profile = None
    legacy_profile_bootstrap = False
    legacy_profile_absent = False

    if base_execution_profile_text is not None:
        try:
            base_execution_profile = load_profile_text(base_execution_profile_text)
        except ProfileError as exc:
            reasons.append(f"EXECUTION_PROFILE_BASE_INVALID:{exc}")
    if head_execution_profile_text is not None:
        try:
            head_execution_profile = load_profile_text(head_execution_profile_text)
        except ProfileError as exc:
            reasons.append(f"EXECUTION_PROFILE_HEAD_INVALID:{exc}")

    if base_execution_profile_text is None and head_execution_profile is not None:
        authority_contract = head_execution_profile.get("authority_contract")
        if authority_contract == "legacy-v2":
            legacy_profile_bootstrap = True
        elif authority_contract == "profile-v3":
            profile_id = str(head_execution_profile.get("profile_id") or "")
            durable_post_bridge = _has_durable_stage_a_bridge(
                root, base, profile_id
            ) or _has_durable_profile_v3_snapshot(root, base, profile_id)
            if base_epoch < 3 or not durable_post_bridge:
                reasons.append("EXECUTION_PROFILE_STAGE_A_EVIDENCE_MISSING")
        else:
            reasons.append("EXECUTION_PROFILE_BOOTSTRAP_CONTRACT_INVALID")
    elif base_execution_profile is not None and head_execution_profile is not None:
        reasons.extend(profile_transition_reasons(base_execution_profile_text, head_execution_profile_text))
    elif base_execution_profile_text is not None and head_execution_profile_text is None:
        reasons.append("EXECUTION_PROFILE_HEAD_MISSING")
    elif base_execution_profile_text is None and head_execution_profile_text is None:
        # Backward-compatible fixture/pre-bridge evaluation only. Once a base owns
        # a profile, deleting it is covered by the branch above and fails closed.
        legacy_profile_absent = True
        try:
            canonical_text = (Path(__file__).resolve().parents[1] / ".engineering/execution-profile.yaml").read_text(encoding="utf-8")
            head_execution_profile = load_profile_text(canonical_text)
        except (OSError, ProfileError) as exc:
            reasons.append(f"EXECUTION_PROFILE_FALLBACK_INVALID:{exc}")

    active_execution_profile = head_execution_profile or base_execution_profile

    if head_epoch < base_epoch:
        reasons.append(f"GOVERNANCE_POLICY_EPOCH_REGRESSION:base={base_epoch}:head={head_epoch}")

    epoch_guarded_surfaces = PROTECTED_GOVERNANCE_SURFACES + EPOCH_GUARDED_GOVERNANCE_SURFACES + (
        CANONICAL_EPOCH_GUARDED_GOVERNANCE_SURFACES if mode == "canonical" else ()
    )
    root_migration_surfaces = set(EPOCH_GUARDED_GOVERNANCE_SURFACES)
    if mode == "canonical":
        root_migration_surfaces.update(CANONICAL_EPOCH_GUARDED_GOVERNANCE_SURFACES)
    if legacy_profile_bootstrap:
        # Stage-A's one-time legacy-v2 bridge predates these managed surfaces, so
        # the old base helper cannot bind them. A direct profile-v3 restoration is
        # different: it must stay root-migration-bound and is never exempt here.
        bootstrap_new_surfaces = set(EXECUTION_PROFILE_SURFACES) | set(POST_BRIDGE_CANONICAL_SURFACES)
        root_migration_surfaces.difference_update(bootstrap_new_surfaces)
    if legacy_profile_absent:
        pre_bridge_only = set(EXECUTION_PROFILE_SURFACES) | set(POST_BRIDGE_CANONICAL_SURFACES)
        epoch_guarded_surfaces = tuple(path for path in epoch_guarded_surfaces if path not in pre_bridge_only)
        root_migration_surfaces.difference_update(pre_bridge_only)

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
                reasons.append(f"GOVERNANCE_SURFACE_CHANGED_WITHOUT_POLICY_EPOCH:{path}")

    if changed_root_surfaces and head_epoch > base_epoch:
        reasons.extend(_root_migration_reasons(root, base, head, base_epoch, head_epoch, changed_root_surfaces))

    if mode == "adopted":
        reasons.extend(_adopted_workflow_reasons(_read_at(root, head, ADOPTED_ENGINEERING_WORKFLOW), head_profile))
    elif mode == "canonical":
        reasons.extend(_canonical_floor_workflow_reasons(_read_at(root, head, GOVERNANCE_REUSABLE_WORKFLOW)))
        reasons.extend(_canonical_validate_reasons(_read_at(root, head, CANONICAL_VALIDATE_WORKFLOW)))
    else:
        reasons.append("GOVERNANCE_PROJECT_MODE_INVALID")

    if active_execution_profile is not None:
        for path in MANAGED_EXECUTION_SURFACES:
            content = _read_at(root, head, path)
            if content is None:
                reasons.append(f"MANAGED_GOVERNANCE_PATH_MISSING:{path}")
                continue
            reasons.extend(_execution_surface_reasons(path, content, active_execution_profile))
        for path in retired_artifact_paths(active_execution_profile):
            if _tree_has_path(root, head, path):
                reasons.append(f"RETIRED_RUNTIME_ARTIFACT_REINTRODUCED:{path}")

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
