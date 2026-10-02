#!/usr/bin/env python3
"""Provider-neutral execution-profile authority and compatibility helpers."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import yaml

PROFILE_PATH = ".engineering/execution-profile.yaml"
PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RUNTIME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
EFFECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
META_RE = re.compile(r"^([A-Z][A-Z0-9_]+)=(.*)$")


class ProfileError(ValueError):
    pass


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileError(f"{label}_INVALID")
    return value
def _string_list(value: Any, label: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
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


def load_profile_text(text: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ProfileError("PROFILE_YAML_INVALID") from exc
    data = _mapping(raw, "PROFILE")
    allowed = {
        "contract_version", "profile_id", "revision", "authority_contract", "runtime",
        "packet_compatibility", "retired_surface", "effect_policy", "policy_migration",
    }
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ProfileError("PROFILE_UNKNOWN_KEY:" + unknown[0])
    if data.get("contract_version") != 1:
        raise ProfileError("PROFILE_CONTRACT_VERSION_INVALID")
    profile_id = data.get("profile_id")
    if not isinstance(profile_id, str) or PROFILE_ID_RE.fullmatch(profile_id) is None:
        raise ProfileError("PROFILE_ID_INVALID")
    revision = data.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ProfileError("PROFILE_REVISION_INVALID")

    authority_contract = data.get("authority_contract")
    if authority_contract not in {"legacy-v2", "profile-v3"}:
        raise ProfileError("PROFILE_AUTHORITY_CONTRACT_INVALID")

    runtime = _mapping(data.get("runtime"), "PROFILE_RUNTIME")
    if set(runtime) != {"primary", "optional_reviewers", "disabled"}:
        raise ProfileError("PROFILE_RUNTIME_KEYS_INVALID")
    primary = runtime.get("primary")
    if not isinstance(primary, str) or RUNTIME_ID_RE.fullmatch(primary) is None:
        raise ProfileError("PROFILE_PRIMARY_RUNTIME_INVALID")
    reviewers = _string_list(
        runtime.get("optional_reviewers"), "PROFILE_OPTIONAL_REVIEWERS", RUNTIME_ID_RE
    )
    disabled = _string_list(
        runtime.get("disabled"), "PROFILE_DISABLED_RUNTIMES", RUNTIME_ID_RE
    )
    if primary in disabled:
        raise ProfileError("PROFILE_PRIMARY_RUNTIME_DISABLED")
    compat = _mapping(data.get("packet_compatibility"), "PROFILE_PACKET_COMPATIBILITY")
    if set(compat) != {"legacy_v2_implementers"}:
        raise ProfileError("PROFILE_PACKET_COMPATIBILITY_KEYS_INVALID")
    legacy = compat.get("legacy_v2_implementers")
    if not isinstance(legacy, dict):
        raise ProfileError("PROFILE_LEGACY_IMPLEMENTERS_INVALID")
    normalized_legacy: dict[str, str] = {}
    for key, target in legacy.items():
        if (
            not isinstance(key, str) or RUNTIME_ID_RE.fullmatch(key) is None
            or not isinstance(target, str) or PROFILE_ID_RE.fullmatch(target) is None
        ):
            raise ProfileError("PROFILE_LEGACY_IMPLEMENTERS_INVALID")
        if target != profile_id:
            raise ProfileError("PROFILE_LEGACY_IMPLEMENTER_TARGET_INVALID")
        if key in disabled:
            raise ProfileError("PROFILE_LEGACY_IMPLEMENTER_DISABLED")
        normalized_legacy[key] = target

    retired = _mapping(data.get("retired_surface"), "PROFILE_RETIRED_SURFACE")
    if set(retired) != {"artifact_paths", "text_patterns", "remove_exact_text"}:
        raise ProfileError("PROFILE_RETIRED_SURFACE_KEYS_INVALID")
    artifacts = retired.get("artifact_paths")
    patterns = retired.get("text_patterns")
    removals = retired.get("remove_exact_text")
    if (
        not isinstance(artifacts, list)
        or any(
            not isinstance(x, str) or not x or Path(x).is_absolute() or ".." in Path(x).parts
            for x in artifacts
        )
    ):
        raise ProfileError("PROFILE_RETIRED_ARTIFACTS_INVALID")
    if len(artifacts) != len(set(artifacts)):
        raise ProfileError("PROFILE_RETIRED_ARTIFACTS_DUPLICATE")
    if not isinstance(patterns, list) or any(not isinstance(x, str) or not x for x in patterns):
        raise ProfileError("PROFILE_RETIRED_PATTERNS_INVALID")
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ProfileError("PROFILE_RETIRED_PATTERN_INVALID") from exc
    if not isinstance(removals, list) or any(not isinstance(x, str) or not x for x in removals):
        raise ProfileError("PROFILE_RETIRED_REMOVALS_INVALID")

    migration = _mapping(data.get("policy_migration"), "PROFILE_POLICY_MIGRATION")
    if set(migration) != {"legacy_execution_profile_markers", "legacy_external_write_markers"}:
        raise ProfileError("PROFILE_POLICY_MIGRATION_KEYS_INVALID")
    legacy_profile_markers = migration.get("legacy_execution_profile_markers")
    legacy_write_markers = migration.get("legacy_external_write_markers")
    if not isinstance(legacy_profile_markers, list) or any(not isinstance(x, str) or not x for x in legacy_profile_markers):
        raise ProfileError("PROFILE_LEGACY_EXECUTION_MARKERS_INVALID")
    if not isinstance(legacy_write_markers, list) or any(not isinstance(x, str) or not x for x in legacy_write_markers):
        raise ProfileError("PROFILE_LEGACY_WRITE_MARKERS_INVALID")

    effect_policy = _mapping(data.get("effect_policy"), "PROFILE_EFFECT_POLICY")
    if set(effect_policy) != {"trusted_boundary_required"}:
        raise ProfileError("PROFILE_EFFECT_POLICY_KEYS_INVALID")
    high_risk = _string_list(
        effect_policy.get("trusted_boundary_required"),
        "PROFILE_TRUSTED_BOUNDARY_EFFECTS",
        EFFECT_ID_RE,
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
        "packet_compatibility": {"legacy_v2_implementers": normalized_legacy},
        "retired_surface": {
            "artifact_paths": tuple(artifacts),
            "text_patterns": tuple(patterns),
            "remove_exact_text": tuple(removals),
        },
        "effect_policy": {"trusted_boundary_required": high_risk},
        "policy_migration": {
            "legacy_execution_profile_markers": tuple(legacy_profile_markers),
            "legacy_external_write_markers": tuple(legacy_write_markers),
        },
    }


def load_profile(root: Path | str = ".") -> dict[str, Any]:
    path = Path(root).resolve() / PROFILE_PATH
    try:
        return load_profile_text(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError("PROFILE_UNAVAILABLE") from exc
def profile_identity(profile: dict[str, Any]) -> tuple[str, int]:
    return str(profile["profile_id"]), int(profile["revision"])


def profile_transition_reasons(base_text: str, head_text: str) -> list[str]:
    try:
        base = load_profile_text(base_text)
        head = load_profile_text(head_text)
    except ProfileError as exc:
        return [str(exc)]
    if base_text == head_text:
        return []
    if head["revision"] != base["revision"] + 1:
        return ["EXECUTION_PROFILE_REVISION_NOT_INCREMENTED"]
    return []


def packet_authority(
    profile: dict[str, Any], metadata: dict[str, str]
) -> tuple[list[str], list[str]]:
    blocking: list[str] = []
    warnings: list[str] = []
    version = metadata.get("PACKET_VERSION", "")
    selected_id, selected_revision = profile_identity(profile)
    if version == "3":
        if "IMPLEMENTER" in metadata:
            blocking.append("EXECUTION_AUTHORITY_AMBIGUOUS")
        if metadata.get("EXECUTION_PROFILE") != selected_id:
            blocking.append("EXECUTION_PROFILE_ID_MISMATCH")
        revision = metadata.get("EXECUTION_PROFILE_REVISION", "")
        if not revision.isdigit() or int(revision) < 1:
            blocking.append("EXECUTION_PROFILE_REVISION_INVALID")
        elif int(revision) != selected_revision:
            blocking.append("EXECUTION_PROFILE_REVISION_MISMATCH")
    elif version == "2":
        if "EXECUTION_PROFILE" in metadata or "EXECUTION_PROFILE_REVISION" in metadata:
            blocking.append("EXECUTION_AUTHORITY_AMBIGUOUS")
        legacy = metadata.get("IMPLEMENTER", "")
        mapped = profile["packet_compatibility"]["legacy_v2_implementers"].get(legacy)
        if mapped != selected_id:
            blocking.append("LEGACY_PACKET_IMPLEMENTER_MISMATCH")
        elif not blocking:
            warnings.append("LEGACY_EXECUTION_PROFILE_COMPAT")
    else:
        blocking.append("PACKET_PROFILE_AUTHORITY_UNSUPPORTED")
    return sorted(set(blocking)), sorted(set(warnings))


def retired_rule_present(text: str, profile: dict[str, Any]) -> bool:
    return any(
        re.search(pattern, text) is not None
        for pattern in profile["retired_surface"]["text_patterns"]
    )
def rewrite_retired_text(text: str, profile: dict[str, Any]) -> str:
    out = text
    for old in profile["retired_surface"]["remove_exact_text"]:
        out = out.replace(old, "")
    return out


def retired_artifact_paths(profile: dict[str, Any]) -> tuple[str, ...]:
    return tuple(profile["retired_surface"]["artifact_paths"])


def requires_trusted_boundary(effect_class: str, profile: dict[str, Any]) -> bool:
    return effect_class in set(profile["effect_policy"]["trusted_boundary_required"])


def parse_packet_metadata(text: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("## "):
            break
        match = META_RE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key in metadata:
            raise ProfileError(f"PACKET_METADATA_DUPLICATE:{key}")
        metadata[key] = value
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--root", default=".")
    auth = sub.add_parser("authorize-packet")
    auth.add_argument("--root", default=".")
    auth.add_argument("--body-file", required=True)
    effect = sub.add_parser("classify-effect")
    effect.add_argument("--root", default=".")
    effect.add_argument("--effect-class", required=True)
    args = parser.parse_args()
    try:
        profile = load_profile(args.root)
        if args.command == "authorize-packet":
            metadata = parse_packet_metadata(
                Path(args.body_file).read_text(encoding="utf-8")
            )
            blocking, warnings = packet_authority(profile, metadata)
            for warning in warnings:
                print("WARNING=" + warning)
            if blocking:
                print("EXECUTION_PROFILE_AUTHORITY=BLOCK")
                for reason in blocking:
                    print("REASON=" + reason)
                return 3
            print("EXECUTION_PROFILE_AUTHORITY=PASS")
            return 0
        if args.command == "classify-effect":
            required = requires_trusted_boundary(args.effect_class, profile)
            print("TRUSTED_BOUNDARY_REQUIRED=" + ("YES" if required else "NO"))
            return 0
        profile_id, revision = profile_identity(profile)
        print("EXECUTION_PROFILE=PASS")
        print(f"PROFILE_ID={profile_id}")
        print(f"PROFILE_REVISION={revision}")
        return 0
    except (ProfileError, OSError, UnicodeError) as exc:
        print(f"EXECUTION_PROFILE=BLOCK reason={exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
