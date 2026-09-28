#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/engineering-context.py"
spec = importlib.util.spec_from_file_location("engineering_context_tested", MODULE_PATH)
assert spec and spec.loader
ctx = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ctx
spec.loader.exec_module(ctx)


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()
def make_repo() -> Path:
    root = Path(tempfile.mkdtemp(prefix="engineering-context-test-"))
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Engineering Context Test")
    (root / "tools").mkdir()
    (root / "standards").mkdir()
    (root / ".engineering").mkdir()
    (root / "docs").mkdir()
    (root / "scratch").mkdir()
    (root / "AGENTS.md").write_text("mandatory router\n", encoding="utf-8")
    (root / ".engineering/project.yaml").write_text("project:\n  name: fixture\n", encoding="utf-8")
    (root / ".gitignore").write_text("scratch/\n", encoding="utf-8")
    (root / "tools/context_router.py").write_text(
        "SOURCE_SENTINEL_SHOULD_NEVER_APPEAR = True\n", encoding="utf-8"
    )
    (root / "tools/unrelated_release.py").write_text("pass\n", encoding="utf-8")
    (root / "standards/SESSION_CONTINUITY.md").write_text(
        "canonical session context rules\n", encoding="utf-8"
    )
    (root / "docs/hotel.md").write_text("unrelated travel notes\n", encoding="utf-8")
    (root / "docs/컨텍스트.md").write_text("unicode path fixture\n", encoding="utf-8")
    (root / ".engineering/knowledge.yaml").write_text(
        "version: 1\n"
        "domains:\n"
        "  - id: session-continuity\n"
        "    summary: Repository context routing and durable session resume.\n"
        "    canonical:\n"
        "      - standards/SESSION_CONTINUITY.md\n",
        encoding="utf-8",
    )
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture")
    return root
def run_cli(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(MODULE_PATH), "--root", str(root), *extra],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_orientation_paths(output: str) -> list[str]:
    result: list[str] = []
    for line in output.splitlines():
        if not line.startswith("ORIENTATION_FILE_JSON="):
            continue
        encoded = line.removeprefix("ORIENTATION_FILE_JSON=").split(" REASON=", 1)[0]
        result.append(json.loads(encoded))
    return result


def test_orientation_is_exact_head_bounded_and_content_free() -> None:
    root = make_repo()
    (root / "scratch/context_router_secret.py").write_text("ignored\n", encoding="utf-8")
    expected_head = git(root, "rev-parse", "HEAD")
    first = run_cli(root, "--task", "context routing", "--max-orientation", "2")
    second = run_cli(root, "--task", "context routing", "--max-orientation", "2")
    if first.returncode != 0 or second.returncode != 0:
        fail(f"orientation CLI failed: {first.stderr} {second.stderr}")
    if first.stdout != second.stdout:
        fail("same exact HEAD and task did not produce byte-stable output")
    if f"ORIENTATION_HEAD={expected_head}" not in first.stdout:
        fail("orientation report is not bound to exact HEAD")
    if "ORIENTATION_COUNT=2" not in first.stdout:
        fail(f"orientation result was not bounded: {first.stdout}")
    paths = parse_orientation_paths(first.stdout)
    if len(paths) != 2:
        fail(f"wrong orientation path count: {paths}")
    tracked = set(git(root, "ls-files").splitlines())
    if any(path not in tracked for path in paths):
        fail(f"orientation emitted a non-tracked path: {paths}")
    if "standards/SESSION_CONTINUITY.md" not in paths:
        fail(f"domain canonical path was not oriented: {paths}")
    if "tools/context_router.py" not in paths:
        fail(f"task-relevant path was not oriented: {paths}")
    if "docs/hotel.md" in paths or "scratch/context_router_secret.py" in paths:
        fail(f"unrelated or ignored path leaked into orientation: {paths}")
    if "SOURCE_SENTINEL_SHOULD_NEVER_APPEAR" in first.stdout:
        fail("orientation leaked source content")
    if str(root) in first.stdout:
        fail("orientation leaked an absolute repository path")


def test_orientation_limit_and_no_match() -> None:
    root = make_repo()
    limited = run_cli(root, "--task", "context routing", "--max-orientation", "1")
    if limited.returncode != 0 or "ORIENTATION_COUNT=1" not in limited.stdout:
        fail(f"max orientation bound failed: {limited.stdout} {limited.stderr}")
    no_match = run_cli(root, "--task", "zebrafjord", "--max-orientation", "3")
    if no_match.returncode != 0:
        fail(f"no-match orientation failed: {no_match.stderr}")
    if "ORIENTATION_DECISION=NO_MATCH" not in no_match.stdout or "ORIENTATION_COUNT=0" not in no_match.stdout:
        fail(f"no-match decision drifted: {no_match.stdout}")


def test_unicode_task_terms_are_supported() -> None:
    root = make_repo()
    run = run_cli(root, "--task", "컨텍스트")
    if run.returncode != 0:
        fail(f"unicode task failed: {run.stderr}")
    paths = parse_orientation_paths(run.stdout)
    if "docs/컨텍스트.md" not in paths:
        fail(f"unicode task/path relevance was lost: {paths}")


def test_dirty_worktree_fails_closed() -> None:
    root = make_repo()
    (root / "tools/context_router.py").write_text("dirty\n", encoding="utf-8")
    run = run_cli(root, "--task", "context routing")
    if run.returncode == 0:
        fail("dirty worktree orientation unexpectedly passed")
    if "orientation requires a clean worktree" not in run.stderr:
        fail(f"wrong dirty-worktree failure: {run.stderr}")
def test_orientation_rejects_head_change_during_scan() -> None:
    root = make_repo()
    original = ctx.tracked_files

    def changing_inventory(repo: Path) -> list[str]:
        paths = original(repo)
        (repo / "tools/concurrent.py").write_text("pass\n", encoding="utf-8")
        git(repo, "add", "tools/concurrent.py")
        git(repo, "commit", "-m", "concurrent change")
        return paths

    ctx.tracked_files = changing_inventory
    try:
        try:
            ctx.orientation(root, "context routing", 2)
        except SystemExit as exc:
            if "orientation repository state changed during scan" not in str(exc):
                fail(f"wrong concurrent-head failure: {exc}")
        else:
            fail("orientation accepted candidates from a different HEAD")
    finally:
        ctx.tracked_files = original


def test_legacy_router_output_is_unchanged_without_task() -> None:
    root = make_repo()
    run = run_cli(root)
    if run.returncode != 0:
        fail(f"legacy context router failed: {run.stderr}")
    required = (
        "CONTEXT_BASE=main",
        "CHANGED_COUNT=0",
        "AFFECTED_DOMAINS=<none>",
        "READ=AGENTS.md",
        "READ=.engineering/project.yaml",
        "CONTEXT_ROUTER=PASS",
    )
    for token in required:
        if token not in run.stdout:
            fail(f"legacy context output missing {token}: {run.stdout}")
    if "ORIENTATION_" in run.stdout:
        fail("orientation output appeared without --task")


def test_invalid_orientation_inputs_fail_closed() -> None:
    root = make_repo()
    too_many = run_cli(root, "--task", "context", "--max-orientation", "41")
    if too_many.returncode == 0:
        fail("out-of-bound max orientation was accepted")
    empty_terms = run_cli(root, "--task", "!")
    if empty_terms.returncode == 0:
        fail("task without usable terms was accepted")


def main() -> int:
    tests = [
        test_orientation_is_exact_head_bounded_and_content_free,
        test_orientation_limit_and_no_match,
        test_unicode_task_terms_are_supported,
        test_dirty_worktree_fails_closed,
        test_orientation_rejects_head_change_during_scan,
        test_legacy_router_output_is_unchanged_without_task,
        test_invalid_orientation_inputs_fail_closed,
    ]
    for test in tests:
        test()
    print("ENGINEERING_CONTEXT_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
