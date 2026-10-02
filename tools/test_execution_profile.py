#!/usr/bin/env python3
from __future__ import annotations

import copy
from pathlib import Path

import yaml

import execution_profile as ep

ROOT = Path(__file__).resolve().parents[1]


def synthetic_profile() -> dict:
    current = yaml.safe_load((ROOT / ep.PROFILE_PATH).read_text(encoding="utf-8"))
    current["profile_id"] = "synthetic-profile"
    current["revision"] = 7
    current["authority_contract"] = "profile-v3"
    current["runtime"]["primary"] = "SYNTHETIC_RUNTIME"
    current["runtime"]["disabled"] = ["OLD_RUNTIME"]
    current["runtime"]["optional_reviewers"] = []
    current["packet_compatibility"]["legacy_v2_implementers"] = {
        "LEGACY_SYNTH": "synthetic-profile"
    }
    current["retired_surface"]["artifact_paths"] = [".old-runtime"]
    current["retired_surface"]["text_patterns"] = [r"(?i)OLD_RUNTIME"]
    current["retired_surface"]["remove_exact_text"] = [
        "legacy old runtime instruction"
    ]
    return current
def test_current_profile() -> None:
    profile = ep.load_profile(ROOT)
    profile_id, revision = ep.profile_identity(profile)
    primary = profile["runtime"]["primary"]
    assert profile["authority_contract"] == "legacy-v2"
    assert primary not in profile["runtime"]["disabled"]

    blocking, warnings = ep.packet_authority(
        profile,
        {
            "PACKET_VERSION": "3",
            "EXECUTION_PROFILE": profile_id,
            "EXECUTION_PROFILE_REVISION": str(revision),
        },
    )
    assert blocking == [] and warnings == []

    legacy_key = next(
        iter(profile["packet_compatibility"]["legacy_v2_implementers"])
    )
    blocking, warnings = ep.packet_authority(
        profile, {"PACKET_VERSION": "2", "IMPLEMENTER": legacy_key}
    )
    assert blocking == []
    assert warnings == ["LEGACY_EXECUTION_PROFILE_COMPAT"]
    assert ep.requires_trusted_boundary("production", profile)
    assert not ep.requires_trusted_boundary("ordinary_repo_write", profile)
def test_synthetic_profile_is_core_neutral() -> None:
    raw = synthetic_profile()
    synthetic = ep.load_profile_text(yaml.safe_dump(raw, sort_keys=False))
    blocking, warnings = ep.packet_authority(
        synthetic,
        {
            "PACKET_VERSION": "3",
            "EXECUTION_PROFILE": "synthetic-profile",
            "EXECUTION_PROFILE_REVISION": "7",
        },
    )
    assert blocking == [] and warnings == []
    assert ep.retired_rule_present("start OLD_RUNTIME", synthetic)
    rewritten = ep.rewrite_retired_text(
        "x legacy old runtime instruction y", synthetic
    )
    assert rewritten.strip() == "x  y"

    mismatch, _ = ep.packet_authority(
        ep.load_profile(ROOT),
        {
            "PACKET_VERSION": "3",
            "EXECUTION_PROFILE": "other",
            "EXECUTION_PROFILE_REVISION": "1",
        },
    )
    assert "EXECUTION_PROFILE_ID_MISMATCH" in mismatch
def test_transition_and_disabled_primary() -> None:
    raw = synthetic_profile()
    disabled = copy.deepcopy(raw)
    disabled["runtime"]["primary"] = "OLD_RUNTIME"
    try:
        ep.load_profile_text(yaml.safe_dump(disabled, sort_keys=False))
    except ep.ProfileError as exc:
        assert str(exc) == "PROFILE_PRIMARY_RUNTIME_DISABLED"
    else:
        raise AssertionError("disabled primary runtime accepted")

    bumped = copy.deepcopy(raw)
    bumped["revision"] = 8
    assert ep.profile_transition_reasons(
        yaml.safe_dump(raw, sort_keys=False),
        yaml.safe_dump(bumped, sort_keys=False),
    ) == []

    changed = copy.deepcopy(raw)
    changed["runtime"]["primary"] = "ANOTHER_RUNTIME"
    reasons = ep.profile_transition_reasons(
        yaml.safe_dump(raw, sort_keys=False),
        yaml.safe_dump(changed, sort_keys=False),
    )
    assert "EXECUTION_PROFILE_REVISION_NOT_INCREMENTED" in reasons


def main() -> int:
    test_current_profile()
    test_synthetic_profile_is_core_neutral()
    test_transition_and_disabled_primary()
    print("EXECUTION_PROFILE_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
