#!/usr/bin/env python3
"""Executable regressions for terminal notification delivery verification."""
from __future__ import annotations

import contextlib
import io
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import terminal_completion_notify as notify

ROOT = Path(__file__).resolve().parents[1]
T = ROOT / "tools/terminal_completion_notify.py"
HEAD = "a" * 40
REPO = "datarelay-labs/engineering-system"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(T), *args], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def terminal_result(output: str, returncode: int = 0, *, trusted: bool = True) -> tuple[int, str]:
    """Stub every host/delivery boundary; these tests never send notifications."""
    argv = [str(T), "--repository", REPO, "--workstream", "w", "--head", HEAD, "--summary", "test"]
    captured = io.StringIO()
    completed = subprocess.CompletedProcess([], returncode, output)
    with (
        patch.object(sys, "argv", argv),
        patch.object(notify, "trusted", return_value=trusted),
        patch.object(notify.subprocess, "check_output", side_effect=[HEAD, f"https://github.com/{REPO}.git"]),
        patch.object(notify.subprocess, "run", return_value=completed) as delivery,
        contextlib.redirect_stdout(captured),
    ):
        rc = notify.main()
        if not trusted:
            delivery.assert_not_called()
    return rc, captured.getvalue()


def test_invalid_identity() -> None:
    bad = run("--repository", "bad", "--workstream", "w", "--head", HEAD, "--summary", "x")
    assert bad.returncode == 3
    assert "OWNER_NOTIFICATION=RETRY_PENDING reason=identity" in bad.stdout
    assert run("--repository", REPO, "--workstream", "bad space", "--head", HEAD, "--summary", "x").returncode == 3
    assert run("--repository", REPO, "--workstream", "w", "--head", "short", "--summary", "x").returncode == 3


def test_exact_success_marker() -> None:
    for marker in (
        "", "OWNER_NOTIFY=PASSIVE", "NOT_OWNER_NOTIFY=PASS",
        "diagnostic: OWNER_NOTIFY=PASS", "OWNER_NOTIFY=PASS suffix",
        " OWNER_NOTIFY=PASS", "OWNER_NOTIFY=PASS ",
        "OWNER_NOTIFY=PASS=unexpected", "OWNER_NOTIFY=FAIL",
    ):
        rc, output = terminal_result(marker + "\nOWNER_NOTIFY_RECEIPT=test-receipt\n")
        assert rc == 3, (marker, rc, output)
        assert "OWNER_NOTIFICATION=RETRY_PENDING reason=delivery_unverified" in output
        assert "OWNER_NOTIFICATION=PASS" not in output
    for output in (
        "OWNER_NOTIFY=PASS\nOWNER_NOTIFY_RECEIPT=test-receipt\n",
        "diagnostic\nOWNER_NOTIFY=PASS\nOWNER_NOTIFY_RECEIPT=test-receipt\ntrailing log",
        "OWNER_NOTIFY=PASS\r\nOWNER_NOTIFY_RECEIPT=test-receipt\r\n",
    ):
        rc, result = terminal_result(output)
        assert rc == 0, result
        assert "OWNER_NOTIFICATION=PASS OWNER_NOTIFICATION_RECEIPT=PASS" in result
        assert "receipt=test-receipt" in result


def test_delivery_boundaries() -> None:
    valid = "OWNER_NOTIFY=PASS\nOWNER_NOTIFY_RECEIPT=test-receipt\n"
    rc, result = terminal_result(valid, returncode=1)
    assert rc == 3 and "reason=delivery_unverified" in result
    for receipt in ("", "bad receipt", "x" * 257):
        rc, result = terminal_result("OWNER_NOTIFY=PASS\nOWNER_NOTIFY_RECEIPT=" + receipt + "\n")
        assert rc == 3 and "reason=delivery_unverified" in result
    rc, result = terminal_result(valid, trusted=False)
    assert rc == 3 and "reason=trusted_helper" in result


def main() -> None:
    test_invalid_identity()
    test_exact_success_marker()
    test_delivery_boundaries()
    src = T.read_text()
    for token in ("/usr/lib/engineering-system/owner-notify", "/usr/bin/sudo", "OWNER_NOTIFICATION=RETRY_PENDING", "OWNER_NOTIFICATION=PASS", "OWNER_NOTIFICATION_RECEIPT=PASS", "OWNER_NOTIFY=PASS", "COMPLETE"):
        assert token in src
    print("TERMINAL_COMPLETION_NOTIFY_TESTS=PASS")


if __name__ == "__main__":
    main()
