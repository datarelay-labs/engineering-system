#!/usr/bin/env python3
"""Deterministic regressions for Work Packet author permission enforcement."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from work_packet_authority import (  # noqa: E402
    AUTHORIZED_WORK_PACKET_PERMISSIONS,
    WORK_PACKET_AUTHOR_UNTRUSTED,
    authorize_work_packet_author_permission,
    permission_from_collaborator_payload,
)


def expect_untrusted(callable_obj, *args) -> None:
    try:
        callable_obj(*args)
    except SystemExit as exc:
        assert str(exc) == WORK_PACKET_AUTHOR_UNTRUSTED, exc
        return
    raise AssertionError("expected WORK_PACKET_AUTHOR_UNTRUSTED")


def test_authorized_permissions_accepted() -> None:
    assert AUTHORIZED_WORK_PACKET_PERMISSIONS == frozenset({"admin", "maintain", "write"})
    for permission in ("admin", "maintain", "write", "ADMIN", " Maintain ", "WRITE"):
        assert authorize_work_packet_author_permission(permission) == permission.strip().lower()


def test_weaker_or_member_style_permissions_rejected() -> None:
    # MEMBER/COLLABORATOR association is irrelevant; effective read/triage/none fail closed.
    for permission in ("read", "triage", "pull", "none", "", None, "MEMBER", "COLLABORATOR", "OWNER"):
        expect_untrusted(authorize_work_packet_author_permission, permission)


def test_permission_lookup_failure_rejected() -> None:
    expect_untrusted(permission_from_collaborator_payload, None)
    expect_untrusted(permission_from_collaborator_payload, {})
    expect_untrusted(permission_from_collaborator_payload, {"role_name": "admin"})
    expect_untrusted(permission_from_collaborator_payload, {"permission": "read"})
    assert permission_from_collaborator_payload({"permission": "admin"}) == "admin"
    assert permission_from_collaborator_payload({"permission": "write"}) == "write"
    assert permission_from_collaborator_payload({"permission": "maintain"}) == "maintain"


def main() -> int:
    test_authorized_permissions_accepted()
    test_weaker_or_member_style_permissions_rejected()
    test_permission_lookup_failure_rejected()
    print("WORK_PACKET_AUTHORITY_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
