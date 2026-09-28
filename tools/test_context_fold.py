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

        text = "tamper target payload\n" * 8
        result = cf.fold_request(repo, request([block("x", "tool", text)], ["x"]))
        marker = marker_from(result, "x")
        path = entry_path(repo, marker)

        expect_error("MARKER_INVALID", lambda: cf.expand(repo, marker + "!"))
        handle, size = cf.parse_marker(marker)
        wrong_size = cf._marker(handle, size + 1)
        expect_error("STORE_ENTRY_CORRUPT", lambda: cf.expand(repo, wrong_size))

        original_bytes = path.read_bytes()
        path.write_bytes(b"X" + original_bytes[1:])
        os.chmod(path, 0o600)
        expect_error("STORE_ENTRY_CORRUPT", lambda: cf.expand(repo, marker))
        path.write_bytes(original_bytes)
        os.chmod(path, 0o600)

        path.unlink()
        expect_error("STORE_ENTRY_MISSING", lambda: cf.expand(repo, marker))


def test_wrong_store_with_copied_entry_fails_keyed_digest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo1 = init_repo(root / "repo1")
        repo2 = init_repo(root / "repo2")
        text = "private keyed content\n" * 6

        result1 = cf.fold_request(repo1, request([block("a", "json", text)], ["a"]))
        marker1 = marker_from(result1, "a")
        cf.fold_request(repo2, request([block("seed", "json", "seed content")], ["seed"]))
        source = entry_path(repo1, marker1)
        handle1, _ = cf.parse_marker(marker1)
        target = cf.entries_directory(repo2, create=True) / f"{handle1}.bin"
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)
        expect_error("STORE_ENTRY_CORRUPT", lambda: cf.expand(repo2, marker1))


def test_private_permissions_and_shared_parent_compatibility() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = init_repo(Path(tmp) / "repo")
        shared_parent = git_dir(repo) / cf.STORE_PARENT
        shared_parent.mkdir(mode=0o755)
        os.chmod(shared_parent, 0o755)

        result = cf.fold_request(
            repo,
            request([block("log", "log", "private payload\n" * 5)], ["log"]),
        )
        marker = marker_from(result, "log")
        store = cf.store_directory(repo, create=False)
        entries = cf.entries_directory(repo, create=False)

        key = store / cf.KEY_NAME
        entry = entry_path(repo, marker)
        if mode(store) != 0o700 or mode(entries) != 0o700:
            fail("private store directories are not mode 0700")
        if mode(key) != 0o600 or mode(entry) != 0o600:
            fail("key/raw entry files are not mode 0600")
        if mode(shared_parent) != 0o755:
            fail("folding rewrote unrelated shared retention parent permissions")
        status = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain"],
            text=True,
        )
        if status:
            fail(f"git-local store leaked into worktree status: {status}")


def test_symlink_boundaries_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = init_repo(root / "repo-parent")
        outside = root / "outside"
        outside.mkdir()
        (git_dir(repo) / cf.STORE_PARENT).symlink_to(outside, target_is_directory=True)
        expect_error(
            "STORE_SYMLINK_FORBIDDEN",
            lambda: cf.fold_request(
                repo, request([block("x", "log", "payload")], ["x"])
            ),
        )


        repo_key = init_repo(root / "repo-key")
        store = cf.store_directory(repo_key, create=True)
        outside_key = root / "outside.key"
        outside_key.write_bytes(b"K" * cf.KEY_BYTES)
        os.chmod(outside_key, 0o600)
        (store / cf.KEY_NAME).symlink_to(outside_key)
        expect_error(
            "STORE_SYMLINK_FORBIDDEN",
            lambda: cf.fold_request(
                repo_key, request([block("x", "log", "payload")], ["x"])
            ),
        )

        repo_entry = init_repo(root / "repo-entry")
        result = cf.fold_request(
            repo_entry,
            request([block("x", "log", "entry payload")], ["x"]),
        )
        marker = marker_from(result, "x")
        entry = entry_path(repo_entry, marker)
        original = entry.read_bytes()
        entry.unlink()
        outside_entry = root / "outside-entry.bin"
        outside_entry.write_bytes(original)
        os.chmod(outside_entry, 0o600)
        entry.symlink_to(outside_entry)
        expect_error("STORE_SYMLINK_FORBIDDEN", lambda: cf.expand(repo_entry, marker))


def test_store_limits_fail_before_raw_partial_write() -> None:
    old_entries = cf.MAX_ENTRIES
    old_total = cf.MAX_TOTAL_BYTES
    old_entry = cf.MAX_ENTRY_BYTES
    try:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "repo-count")
            cf.MAX_ENTRIES = 1
            raw = request(
                [
                    block("a", "log", "alpha payload"),
                    block("b", "log", "beta payload"),
                ],
                ["a", "b"],
            )
            expect_error("STORE_LIMIT_EXCEEDED", lambda: cf.fold_request(repo, raw))
            entries = cf.entries_directory(repo, create=False)
            if entries.exists() and any(entries.iterdir()):
                fail("entry-count rejection left partial raw entries")

        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "repo-total")
            cf.MAX_ENTRIES = 10
            cf.MAX_TOTAL_BYTES = 10
            raw = request(
                [block("a", "log", "123456"), block("b", "log", "abcdef")],
                ["a", "b"],
            )

            expect_error("STORE_LIMIT_EXCEEDED", lambda: cf.fold_request(repo, raw))
            entries = cf.entries_directory(repo, create=False)
            if entries.exists() and any(entries.iterdir()):
                fail("total-byte rejection left partial raw entries")

        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "repo-entry-size")
            cf.MAX_ENTRIES = 10
            cf.MAX_TOTAL_BYTES = 1024
            cf.MAX_ENTRY_BYTES = 5
            raw = request([block("a", "log", "123456")], ["a"])
            expect_error("ENTRY_TOO_LARGE", lambda: cf.fold_request(repo, raw))
            entries = cf.entries_directory(repo, create=False)
            if entries.exists() and any(entries.iterdir()):
                fail("entry-size rejection left partial raw entries")
    finally:
        cf.MAX_ENTRIES = old_entries
        cf.MAX_TOTAL_BYTES = old_total
        cf.MAX_ENTRY_BYTES = old_entry


def test_disabled_bypass_is_exact_and_store_free() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = init_repo(Path(tmp) / "repo")
        raw = request(
            [block("log", "log", "do not store this", reference="tool:private")],
            ["log"],
            enabled=False,
        )

        result = cf.fold_request(repo, raw)
        if result["task"] != raw["task"] or result["blocks"] != raw["blocks"]:
            fail("disabled mode changed input structure")
        if result["telemetry"] != {
            "decision": "BYPASS",
            "requested_count": 1,
            "folded_count": 0,
            "deduplicated_count": 0,
            "original_folded_bytes": 0,
            "marker_bytes": 0,
        }:
            fail(f"disabled telemetry drifted: {result['telemetry']}")
        if cf.store_directory(repo, create=False).exists():
            fail("disabled mode created a store")


def test_input_authority_and_telemetry_are_bounded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = init_repo(Path(tmp) / "repo")
        optional = block(
            "secret-log",
            "log",
            "credential ghp_NOT_REAL_ABCDEFGHIJKLMNOPQRSTUVWXYZ\n",
            reference="/private/tool/output.log",
        )
        result = cf.fold_request(repo, request([optional], ["secret-log"]))
        marker = marker_from(result, "secret-log")
        handle, _ = cf.parse_marker(marker)
        encoded = json.dumps(result["telemetry"], sort_keys=True)

        for forbidden in (
            "ghp_NOT_REAL_ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "/private/tool/output.log",
            "secret-log",
            handle,
            "credential",
        ):
            if forbidden in encoded:
                fail(f"telemetry leaked forbidden value: {forbidden}")

        expect_error(
            "FOLD_ID_INVALID",
            lambda: cf.fold_request(repo, request([optional], ["missing"])),
        )
        expect_error(
            "FOLD_ID_DUPLICATE",
            lambda: cf.fold_request(
                repo, request([optional], ["secret-log", "secret-log"])
            ),
        )
        invalid_work_packet = block(
            "wp",
            "work_packet",
            "cannot be optional",
            protected=False,
        )
        try:
            cf.fold_request(repo, request([invalid_work_packet], ["wp"]))
        except cf.FoldError as exc:
            if not str(exc).startswith(
                "CONTEXT_INPUT_INVALID:WORK_PACKET_KIND_REQUIRES_PROTECTION"
            ):
                fail(f"wrong canonical input failure: {exc}")
        else:
            fail("non-canonical work_packet input was accepted")


def test_purge_is_explicit_and_preserves_local_key_identity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = init_repo(Path(tmp) / "repo")
        raw = request(
            [
                block("a", "log", "first retained payload"),
                block("b", "test", "second retained payload"),
            ],
            ["a", "b"],
        )
        result = cf.fold_request(repo, raw)
        marker = marker_from(result, "a")
        key_before = (cf.store_directory(repo, create=False) / cf.KEY_NAME).read_bytes()
        report = cf.purge(repo)
        if report["entries_deleted"] != 2:
            fail(f"purge count mismatch: {report}")
        if any(cf.entries_directory(repo, create=False).iterdir()):
            fail("purge left raw entries behind")
        expect_error("STORE_ENTRY_MISSING", lambda: cf.expand(repo, marker))

        again = cf.fold_request(
            repo,
            request([block("a2", "log", "first retained payload")], ["a2"]),
        )
        if marker_from(again, "a2") != marker:
            fail("purge unexpectedly changed store-local key identity")
        key_after = (cf.store_directory(repo, create=False) / cf.KEY_NAME).read_bytes()
        if key_after != key_before:
            fail("purge replaced the store key")


def test_cli_round_trip_uses_private_outputs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = init_repo(root / "repo")
        source = root / "input.json"
        folded = root / "folded.json"
        expanded = root / "expanded.txt"
        text = "cli private unicode ✅\n" * 5
        source.write_text(
            json.dumps(
                request(
                    [block("log", "log", text, reference="tool:cli")],
                    ["log"],
                )
            ),
            encoding="utf-8",
        )
        run = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "fold",
                "--root",
                str(repo),
                "--input",
                str(source),
                "--output",
                str(folded),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if run.returncode != 0:
            fail(f"fold CLI failed: {run.stdout} {run.stderr}")
        folded_doc = json.loads(folded.read_text(encoding="utf-8"))
        marker = marker_from(folded_doc, "log")
        run = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "expand",
                "--root",
                str(repo),
                "--marker",
                marker,
                "--output",
                str(expanded),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if run.returncode != 0:
            fail(f"expand CLI failed: {run.stdout} {run.stderr}")
        if expanded.read_text(encoding="utf-8") != text:
            fail("CLI expansion was not byte-exact text")
        if mode(folded) != 0o600 or mode(expanded) != 0o600:
            fail("CLI outputs were not private mode 0600")

        run = subprocess.run(
            [sys.executable, str(TOOL), "purge", "--root", str(repo)],
            text=True,
            capture_output=True,
            check=False,
        )
        if run.returncode != 0:
            fail(f"purge CLI failed: {run.stdout} {run.stderr}")


def test_source_has_no_network_or_model_dependency() -> None:
    source = TOOL.read_text(encoding="utf-8")
    for forbidden in (
        "import requests",
        "import urllib",
        "import socket",
        "openai",
        "anthropic",
        "http://",
        "https://",
    ):
        if forbidden in source:
            fail(f"context fold gained external/model dependency: {forbidden}")


def main() -> int:
    tests = [
        test_protected_block_cannot_fold,
        test_optional_fold_expand_and_marker_privacy,
        test_same_store_deduplicates_and_different_store_changes_handle,
        test_tamper_missing_and_wrong_store_fail_closed,
        test_wrong_store_with_copied_entry_fails_keyed_digest,
        test_private_permissions_and_shared_parent_compatibility,
        test_symlink_boundaries_fail_closed,
        test_store_limits_fail_before_raw_partial_write,
        test_disabled_bypass_is_exact_and_store_free,
        test_input_authority_and_telemetry_are_bounded,
        test_purge_is_explicit_and_preserves_local_key_identity,
        test_cli_round_trip_uses_private_outputs,
        test_source_has_no_network_or_model_dependency,
    ]
    for test in tests:
        test()
    print("CONTEXT_FOLD_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
