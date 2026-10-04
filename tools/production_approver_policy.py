#!/usr/bin/env python3
"""Validation for the host-administered production approver policy."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

POLICY_KIND = "trusted_production_approver_policy"
POLICY_VERSION = 1
MAX_POLICY_BYTES = 32 * 1024
MAX_REPOSITORIES = 64
MAX_APPROVERS_PER_REPOSITORY = 16
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


class PolicyError(Exception):
    pass


def normalize_policy(payload: Any) -> dict[str, frozenset[str]]:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "kind", "repositories"}:
        raise PolicyError("production approver policy fields are invalid")
    if payload.get("schema_version") != POLICY_VERSION or payload.get("kind") != POLICY_KIND:
        raise PolicyError("production approver policy identity is invalid")
    repositories = payload.get("repositories")
    if (
        not isinstance(repositories, dict)
        or not repositories
        or len(repositories) > MAX_REPOSITORIES
    ):
        raise PolicyError("production approver repository map is invalid")
    normalized: dict[str, frozenset[str]] = {}
    for repository, raw_logins in repositories.items():
        if not isinstance(repository, str) or REPOSITORY_RE.fullmatch(repository) is None:
            raise PolicyError("production approver repository identity is invalid")
        if (
            not isinstance(raw_logins, list)
            or not raw_logins
            or len(raw_logins) > MAX_APPROVERS_PER_REPOSITORY
        ):
            raise PolicyError("production approver login list is invalid")
        logins: list[str] = []
        for raw_login in raw_logins:
            if not isinstance(raw_login, str) or LOGIN_RE.fullmatch(raw_login) is None:
                raise PolicyError("production approver login is invalid")
            if raw_login in logins:
                raise PolicyError("production approver login is duplicated")
            logins.append(raw_login)
        normalized[repository] = frozenset(logins)
    return normalized


def load_policy_bytes(raw: bytes) -> dict[str, frozenset[str]]:
    if not raw or len(raw) > MAX_POLICY_BYTES:
        raise PolicyError("production approver policy size is invalid")
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError("production approver policy JSON is invalid") from exc
    return normalize_policy(payload)


def load_policy_file(path: Path) -> dict[str, frozenset[str]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PolicyError("production approver policy is unavailable") from exc
    return load_policy_bytes(raw)
