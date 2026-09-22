#!/usr/bin/env python3
"""Deterministic Work Packet author permission authorization.

Effective repository permission is the only execution authority. GitHub
``author_association`` may be recorded as evidence but must not authorize.
"""
from __future__ import annotations

from typing import Any

AUTHORIZED_WORK_PACKET_PERMISSIONS = frozenset({"admin", "maintain", "write"})
WORK_PACKET_AUTHOR_UNTRUSTED = "WORK_PACKET_AUTHOR_UNTRUSTED"


def normalize_permission(value: Any) -> str:
    return str(value or "").strip().lower()


def authorize_work_packet_author_permission(permission: Any) -> str:
    """Accept only write/maintain/admin; fail closed otherwise.

    Returns the normalized accepted permission. Raises SystemExit with
    ``WORK_PACKET_AUTHOR_UNTRUSTED`` for missing, unknown, weaker, or empty
    permission values.
    """
    normalized = normalize_permission(permission)
    if normalized in AUTHORIZED_WORK_PACKET_PERMISSIONS:
        return normalized
    raise SystemExit(WORK_PACKET_AUTHOR_UNTRUSTED)


def permission_from_collaborator_payload(payload: Any) -> str:
    """Extract and authorize permission from collaborators/{user}/permission JSON."""
    if not isinstance(payload, dict):
        raise SystemExit(WORK_PACKET_AUTHOR_UNTRUSTED)
    if "permission" not in payload:
        raise SystemExit(WORK_PACKET_AUTHOR_UNTRUSTED)
    return authorize_work_packet_author_permission(payload.get("permission"))
