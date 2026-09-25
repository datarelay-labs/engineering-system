#!/usr/bin/env python3
"""Bounded GitHub and Telegram delivery for one already-authorized watch effect.

The production path posts one issue comment through ``gh api`` or one INFO
Telegram message to api.telegram.org. It does not accept a caller command,
URL, token, or chat id. Telegram credentials are host files.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from coordinator_watch_collect import _bounded_env, resolve_trusted_gh

REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ISSUE_RE = re.compile(r"^[0-9]+$")
TELEGRAM_TOKEN_PATH = Path("/etc/engineering-system/telegram-bot-token")
TELEGRAM_CHAT_PATH = Path("/etc/engineering-system/telegram-chat-id")
TELEGRAM_URL_PREFIX = "https://api.telegram.org/bot"
# Test-only directory for host credential files. Production never reads the request or environment for it.
_TEST_TELEGRAM_DIR: Path | None = None


def _host_file(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        st = path.stat()
        if _TEST_TELEGRAM_DIR is None:
            if st.st_uid != 0 or st.st_mode & 0o022:
                return None
            parent = path.parent
            pst = parent.stat()
            if parent.is_symlink() or pst.st_uid != 0 or pst.st_mode & 0o022:
                return None
        elif stat.S_ISLNK(st.st_mode):
            return None
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text or "\n" in text or "\x00" in text or len(text) > 256:
        return None
    return text


def _telegram_paths() -> tuple[Path, Path]:
    if _TEST_TELEGRAM_DIR is not None:
        root = Path(_TEST_TELEGRAM_DIR)
        return root / "telegram-bot-token", root / "telegram-chat-id"
    return TELEGRAM_TOKEN_PATH, TELEGRAM_CHAT_PATH


def telegram_configured() -> bool:
    token_path, chat_path = _telegram_paths()
    return _host_file(token_path) is not None and _host_file(chat_path) is not None


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


def send_telegram_info(text: str) -> dict[str, str]:
    """Send one INFO message. COMPLETE is never sent. The URL host is fixed."""
    if not text or len(text) > 4000 or "\x00" in text or "COMPLETE" in text.split():
        return _not_sent()
    token_path, chat_path = _telegram_paths()
    token = _host_file(token_path)
    chat = _host_file(chat_path)
    if token is None or chat is None:
        return _not_sent()
    url = f"{TELEGRAM_URL_PREFIX}{token}/sendMessage"
    if not url.startswith(TELEGRAM_URL_PREFIX):
        return _not_sent()
    data = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return _ambiguous()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return _ambiguous()
    result = document.get("result") if isinstance(document, dict) else None
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id < 1:
        return _ambiguous()
    return {"outcome": "SUCCEEDED", "receipt": f"telegram:{message_id}", "level": "INFO"}


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
        return send_telegram_info("\n".join(str(line) for line in lines) + "\n")
    if kind not in {"WAKE_COORDINATOR", "RESUME_ADMITTED_WORKER"}:
        return _not_sent()
    repository = str(effect.get("target_repo") or "")
    target = effect.get("mutation_target")
    issue_id = ""
    if isinstance(target, dict):
        issue_id = str(target.get("id") or "")
    body = str(effect.get("mutation_content") or "")
    return send_github_comment(repository, issue_id, body)
