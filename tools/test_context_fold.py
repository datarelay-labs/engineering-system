#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import context_fold as cf

ROOT = TOOLS.parent
TOOL = TOOLS / "context_fold.py"


def fail(message: str) -> None:
    raise AssertionError(message)


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def git_dir(root: Path) -> Path:
    return Path(
        subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
            text=True,
        ).strip()
    ).resolve()


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def block(
    block_id: str,
    kind: str,
    text: str,
    *,
    protected: bool = False,
    protection_class: str | None = None,
    reference: str | None = None,
) -> dict[str, object]:
    return {
        "id": block_id,
        "kind": kind,
        "text": text,
        "reference": reference,
        "protected": protected,
        "protection_class": protection_class,
        "priority": 0,
    }


def request(
    blocks: list[dict[str, object]],
    fold_ids: list[str] | None,
    *,
    enabled: bool = True,
) -> dict[str, object]:
    return {
        "task": "inspect parser output",
        "blocks": blocks,
        "fold_ids": fold_ids,
        "enabled": enabled,
    }


def marker_from(result: dict[str, object], block_id: str) -> str:
    for item in result["blocks"]:
        if item["id"] == block_id:
            return item["text"]
    fail(f"block missing: {block_id}")


def expect_error(code: str, fn) -> None:
    try:
        fn()
    except cf.FoldError as exc:
        if str(exc) != code:
            fail(f"expected {code}, got {exc}")
    else:
        fail(f"expected failure {code}")


def entry_path(root: Path, marker: str) -> Path:
    handle, _size = cf.parse_marker(marker)
    return cf.entries_directory(root, create=False) / f"{handle}.bin"


def test_protected_block_cannot_fold() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = init_repo(Path(tmp) / "repo")
        raw = request(
            [
                block(
                    "goal",
                    "work_packet",
                    "Goal must remain verbatim.",
                    protected=True,
                    protection_class="goal",
                    reference="issue:111",
                )
            ],
            ["goal"],
        )
        expect_error(
            "PROTECTED_BLOCK_FOLD_FORBIDDEN",
            lambda: cf.fold_request(repo, raw),
        )
        if cf.store_directory(repo, create=False).exists():
            fail("protected rejection created a raw-content store")


def test_optional_fold_expand_and_marker_privacy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = init_repo(Path(tmp) / "repo")
        original = "로그\n한글 ✅\nsecret=ghp_NOT_A_REAL_TOKEN_1234567890\n"
        protected = block(
            "constraint",
            "work_packet",
            "Constraint: exact evidence must remain.",
            protected=True,
            protection_class="constraints",
            reference="policy:exact",
        )
        optional = block(
            "log",
            "log",
            original,
            reference="/home/alice/private/tool.log",
        )
        result = cf.fold_request(repo, request([protected, optional], ["log"]))
        if result["blocks"][0] != protected:
            fail("protected block changed during folding")
        marker = marker_from(result, "log")
        if not cf.MARKER_RE.fullmatch(marker):
            fail(f"fold marker invalid: {marker}")
        if cf.expand(repo, marker) != original:
            fail("expanded text did not match exact original")

        raw_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
        for forbidden in (
            original,
            "ghp_NOT_A_REAL_TOKEN_1234567890",
            "/home/alice/private/tool.log",
            "log",
            raw_sha,
        ):
            if forbidden in marker:
                fail(f"marker leaked source metadata/content: {forbidden}")
        telemetry = json.dumps(result["telemetry"], sort_keys=True)
        handle, _ = cf.parse_marker(marker)
        for forbidden in (
            original,
            "ghp_NOT_A_REAL_TOKEN_1234567890",
            "/home/alice/private/tool.log",
            "log",
            handle,
        ):
            if forbidden in telemetry:
                fail(f"telemetry leaked source/handle: {forbidden}")


def test_same_store_deduplicates_and_different_store_changes_handle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo1 = init_repo(root / "repo1")
        repo2 = init_repo(root / "repo2")
        text = "same bytes across stores\n" * 10

        first = cf.fold_request(repo1, request([block("a", "log", text)], ["a"]))
        second = cf.fold_request(repo1, request([block("b", "log", text)], ["b"]))
        marker1 = marker_from(first, "a")
        marker2 = marker_from(second, "b")
        if marker1 != marker2:
            fail("same store/content produced different keyed handle")
        if second["telemetry"]["deduplicated_count"] != 1:
            fail("same-store repeat was not reported as deduplicated")
        entries = list(cf.entries_directory(repo1, create=False).glob("*.bin"))
        if len(entries) != 1:
            fail(f"dedup store contains {len(entries)} entries")

        other = cf.fold_request(repo2, request([block("c", "log", text)], ["c"]))
        marker3 = marker_from(other, "c")
        if marker3 == marker1:
            fail("different local store key produced transferable handle")
        key1 = (cf.store_directory(repo1, create=False) / cf.KEY_NAME).read_bytes()
        key2 = (cf.store_directory(repo2, create=False) / cf.KEY_NAME).read_bytes()
        if key1 == key2:
            fail("independent stores reused a secret key")


def test_tamper_missing_and_wrong_store_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = init_repo(root / "repo")
