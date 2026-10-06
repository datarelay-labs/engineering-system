#!/usr/bin/env python3
"""Bounded GitHub and owner-notification delivery for one already-authorized watch effect.

The production path posts one issue comment through ``gh api`` or one INFO notification through a fixed root-owned adapter. It accepts no caller command, URL, credential, or destination.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from coordinator_watch_collect import _bounded_env, resolve_trusted_gh

REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ISSUE_RE = re.compile(r"^[0-9]+$")
OWNER_NOTIFY_HELPER = Path("/usr/lib/engineering-system/owner-notify")
SUDO = Path("/usr/bin/sudo")
# Test-only helper override. Production always uses the fixed root-owned helper.
_TEST_OWNER_NOTIFY_HELPER: Path | None = None


def _trusted(path: Path) -> bool:
    try:
        st = path.lstat()
        parent = path.parent.lstat()
        if _TEST_OWNER_NOTIFY_HELPER is not None and path == _TEST_OWNER_NOTIFY_HELPER:
            return stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode)
        return (
            stat.S_ISREG(st.st_mode)
            and not stat.S_ISLNK(st.st_mode)
            and st.st_uid == 0
            and not (st.st_mode & 0o022)
            and parent.st_uid == 0
            and not (parent.st_mode & 0o022)
        )
    except OSError:
        return False


def owner_notification_configured() -> bool:
    helper = _TEST_OWNER_NOTIFY_HELPER or OWNER_NOTIFY_HELPER
    return _trusted(helper) and (_TEST_OWNER_NOTIFY_HELPER is not None or _trusted(SUDO))


def github_configured() -> bool:
    return resolve_trusted_gh() is not None


def _not_sent() -> dict[str, str]:
    return {"outcome": "NOT_SENT", "receipt": "", "level": "NONE"}


def _ambiguous() -> dict[str, str]:
    return {"outcome": "AMBIGUOUS", "receipt": "", "level": "NONE"}


def send_github_comment(repository: str, issue_id: str, body: str) -> dict[str, str]:
    """Post one issue comment. The API path is fixed; the body is the bounded effect."""
    if not REPOSITORY_RE.fullmatch(repository) or not ISSUE_RE.fullmatch(issue_id):
        return _not_sent()
    if not body or len(body) > 4000 or "\x00" in body:
        return _not_sent()
    payload = json.dumps({"body": body})
    try:
        binary = resolve_trusted_gh()
        if binary is None:
            return _not_sent()
        completed = subprocess.run(
            [
                str(binary),
                "api",
                "--method",
                "POST",
                f"repos/{repository}/issues/{issue_id}/comments",
                "--input",
                "-",
            ],
            input=payload,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=30,
            env=_bounded_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return _ambiguous()
    if completed.returncode != 0:
        return _ambiguous()
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return _ambiguous()
    comment_id = document.get("id") if isinstance(document, dict) else None
    if not isinstance(comment_id, int) or isinstance(comment_id, bool) or comment_id < 1:
        return _ambiguous()
    return {"outcome": "SUCCEEDED", "receipt": f"github-comment:{comment_id}", "level": "NONE"}


def _success_marker_present(output: str) -> bool:
    return any(line.strip() == "OWNER_NOTIFY=PASS" for line in output.splitlines())


def send_owner_info(text: str) -> dict[str, str]:
    """Send one INFO notification through the fixed host adapter. COMPLETE is never sent."""
    if not text or len(text) > 4000 or "\x00" in text or "COMPLETE" in text.split():
        return _not_sent()
    helper = _TEST_OWNER_NOTIFY_HELPER or OWNER_NOTIFY_HELPER
    if not _trusted(helper) or (_TEST_OWNER_NOTIFY_HELPER is None and not _trusted(SUDO)):
        return _not_sent()
    argv = [str(helper), "INFO", text] if _TEST_OWNER_NOTIFY_HELPER is not None else [str(SUDO), "-n", str(helper), "INFO", text]
    try:
        completed = subprocess.run(
            argv, cwd="/", env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, shell=False, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _ambiguous()
    if completed.returncode != 0:
        return _ambiguous()
    receipt = ""
    for line in completed.stdout.splitlines():
        if line.startswith("OWNER_NOTIFY_RECEIPT="):
            receipt = line.split("=", 1)[1].strip()
    if not _success_marker_present(completed.stdout) or not receipt or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", receipt) is None:
        return _ambiguous()
    return {"outcome": "SUCCEEDED", "receipt": f"owner-notify:{receipt}", "level": "INFO"}


def send_effect(kind: str, effect: dict[str, Any]) -> dict[str, str]:
    """Deliver one typed effect with the production primitive for that kind."""
    if kind == "NOTIFY_OWNER":
        if effect.get("level") != "INFO":
            return _not_sent()
        lines = [
            "LEVEL=INFO",
            f"WATCH_RESULT={effect.get('watch_result')}",
            f"WORKSTREAM={effect.get('workstream')}",
            f"INTENT_REVISION={effect.get('intent_revision')}",
            f"SUBJECT={effect.get('subject_version')}",
        ]
        return send_owner_info("\n".join(str(line) for line in lines) + "\n")
    if kind not in {"WAKE_COORDINATOR", "RESUME_ADMITTED_WORKER"}:
        return _not_sent()
    repository = str(effect.get("target_repo") or "")
    target = effect.get("mutation_target")
    issue_id = ""
    if isinstance(target, dict):
        issue_id = str(target.get("id") or "")
    body = str(effect.get("mutation_content") or "")
    return send_github_comment(repository, issue_id, body)
