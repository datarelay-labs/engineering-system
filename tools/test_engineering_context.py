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
    (root / "services/api").mkdir(parents=True)
    (root / "AGENTS.md").write_text("mandatory router\n", encoding="utf-8")
    (root / "services/api/AGENTS.md").write_text(
        "scoped mandatory api instructions\n", encoding="utf-8"
    )
    (root / ".engineering/project.yaml").write_text("project:\n  name: fixture\n", encoding="utf-8")
    (root / ".gitignore").write_text("scratch/\n", encoding="utf-8")
    (root / "tools/context_router.py").write_text(
        "SOURCE_SENTINEL_SHOULD_NEVER_APPEAR = True\n", encoding="utf-8"
    )
    (root / "tools/unrelated_release.py").write_text("pass\n", encoding="utf-8")
    (root / "tools/large_context.py").write_text(
        "alpha setup\n"
        "unrelated release path\n"
        "def context_router():\n"
        "SLICE_HEAD=ATTACK\n"
        "    routing_target = 'primary'\n"
        "    return routing_target\n"
        "unrelated footer\n"
        "context routing fallback\n",
        encoding="utf-8",
    )
    (root / "standards/SESSION_CONTINUITY.md").write_text(
        "canonical session context rules\n", encoding="utf-8"
    )
    (root / "docs/hotel.md").write_text("unrelated travel notes\n", encoding="utf-8")
    (root / "docs/컨텍스트.md").write_text("컨텍스트 관련 fixture\n", encoding="utf-8")
    (root / "docs/binary.dat").write_bytes(b"binary\x00payload\n")
    (root / "docs/linked.py").symlink_to("../tools/large_context.py")
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


def run_cli_without_site(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-S", str(MODULE_PATH), "--root", str(root), *extra],
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


def parse_slice_records(output: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in output.splitlines():
        if line.startswith("SLICE_LINE_JSON="):
            records.append(json.loads(line.removeprefix("SLICE_LINE_JSON=")))
    return records


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


def test_slice_is_exact_head_deterministic_and_bounded() -> None:
    root = make_repo()
    expected_head = git(root, "rev-parse", "HEAD")
    args = (
        "--task", "routing target",
        "--slice-path", "tools/large_context.py",
        "--max-slice-lines", "5",
        "--slice-context", "1",
    )
    first = run_cli(root, *args)
    second = run_cli(root, *args)
    if first.returncode != 0 or second.returncode != 0:
        fail(f"slice CLI failed: {first.stderr} {second.stderr}")
    if first.stdout != second.stdout:
        fail("same exact HEAD slice was not byte-stable")
    if f"SLICE_HEAD={expected_head}" not in first.stdout:
        fail("slice is not bound to exact HEAD")
    if "SLICE_PATH_JSON=\"tools/large_context.py\"" not in first.stdout:
        fail("slice path metadata drifted")
    records = parse_slice_records(first.stdout)
    if not records or len(records) > 5:
        fail(f"slice line bound failed: {records}")
    line_numbers = [int(item["line"]) for item in records]
    if line_numbers != sorted(line_numbers):
        fail(f"slice lines were not reassembled in source order: {line_numbers}")
    if not any("routing_target" in str(item["text"]) for item in records):
        fail(f"relevant source line missing: {records}")
    if "\nSLICE_HEAD=ATTACK\n" in first.stdout:
        fail("source content injected a slice control record")
    if not any(item["text"] == "SLICE_HEAD=ATTACK" for item in records):
        fail("control-looking source line was not safely JSON encoded")
    if str(root) in first.stdout or "routing target" in first.stdout:
        fail("slice leaked absolute path or task text")

    tiny = run_cli(
        root,
        "--task", "routing target",
        "--slice-path", "tools/large_context.py",
        "--max-slice-lines", "1",
        "--slice-context", "2",
    )
    tiny_records = parse_slice_records(tiny.stdout)
    if tiny.returncode != 0 or len(tiny_records) != 1 or "routing_target" not in str(tiny_records[0]["text"]):
        fail(f"tiny slice did not retain the highest-relevance match: {tiny.stdout} {tiny.stderr}")
    if "SLICE_TRUNCATED=YES" not in tiny.stdout:
        fail(f"tiny slice failed to report truncated relevant context: {tiny.stdout}")

    git(root, "update-index", "--assume-unchanged", "tools/large_context.py")
    (root / "tools/large_context.py").write_text("MUTATED_WORKTREE_SENTINEL\n", encoding="utf-8")
    hidden = run_cli(root, *args)
    if hidden.returncode != 0:
        fail(f"exact-head hidden-worktree probe failed: {hidden.stderr}")
    if hidden.stdout != first.stdout or "MUTATED_WORKTREE_SENTINEL" in hidden.stdout:
        fail("slice read mutable worktree content instead of exact HEAD blob")


def test_slice_rejects_head_change_during_read() -> None:
    root = make_repo()
    original = ctx.exact_head_text

    def changing_blob(repo: Path, head: str, path: str) -> str:
        text = original(repo, head, path)
        (repo / "tools/concurrent_slice.py").write_text("pass\n", encoding="utf-8")
        git(repo, "add", "tools/concurrent_slice.py")
        git(repo, "commit", "-m", "concurrent slice change")
        return text

    ctx.exact_head_text = changing_blob
    try:
        try:
            ctx.slice_context(root, "routing target", "tools/large_context.py", 5, 1)
        except SystemExit as exc:
            if "slice repository state changed during read" not in str(exc):
                fail(f"wrong concurrent slice failure: {exc}")
        else:
            fail("slice accepted content after HEAD changed during read")
    finally:
        ctx.exact_head_text = original


def test_slice_no_match_and_unicode() -> None:
    root = make_repo()
    no_match = run_cli(root, "--task", "zebrafjord", "--slice-path", "tools/large_context.py")
    if no_match.returncode != 0:
        fail(f"slice no-match failed: {no_match.stderr}")
    if "SLICE_DECISION=NO_MATCH" not in no_match.stdout or "SLICE_SELECTED_LINES=0" not in no_match.stdout:
        fail(f"slice no-match decision drifted: {no_match.stdout}")
    if "SLICE_LINE_JSON=" in no_match.stdout:
        fail("slice no-match emitted source content")

    unicode_run = run_cli(root, "--task", "컨텍스트", "--slice-path", "docs/컨텍스트.md")
    if unicode_run.returncode != 0:
        fail(f"unicode slice failed: {unicode_run.stderr}")
    records = parse_slice_records(unicode_run.stdout)
    if not records or "컨텍스트" not in str(records[0]["text"]):
        fail(f"unicode slice relevance was lost: {records}")


def test_slice_fail_closed_boundaries() -> None:
    root = make_repo()
    mandatory = run_cli(root, "--task", "router", "--slice-path", "AGENTS.md")
    if mandatory.returncode == 0 or "mandatory context cannot be sliced" not in mandatory.stderr:
        fail(f"mandatory context slicing was not blocked: {mandatory.stderr}")
    scoped_mandatory = run_cli(
        root, "--task", "api instructions", "--slice-path", "services/api/AGENTS.md"
    )
    if (
        scoped_mandatory.returncode == 0
        or "mandatory context cannot be sliced" not in scoped_mandatory.stderr
    ):
        fail(f"scoped AGENTS slicing was not blocked: {scoped_mandatory.stderr}")
    symlink = run_cli(root, "--task", "context", "--slice-path", "docs/linked.py")
    if symlink.returncode == 0 or "tracked regular file" not in symlink.stderr:
        fail(f"symlink slice was not blocked: {symlink.stderr}")
    binary = run_cli(root, "--task", "binary", "--slice-path", "docs/binary.dat")
    if binary.returncode == 0 or "binary slice file" not in binary.stderr:
        fail(f"binary slice was not blocked: {binary.stderr}")
    unsafe = run_cli(root, "--task", "context", "--slice-path", "../escape.txt")
    if unsafe.returncode == 0 or "unsafe slice path" not in unsafe.stderr:
        fail(f"unsafe slice path was not blocked: {unsafe.stderr}")

    (root / "tools/unrelated_release.py").write_text("dirty\n", encoding="utf-8")
    dirty = run_cli(root, "--task", "context", "--slice-path", "tools/large_context.py")
    if dirty.returncode == 0 or "slice requires a clean worktree" not in dirty.stderr:
        fail(f"dirty slice was not blocked: {dirty.stderr}")


def test_slice_size_and_line_bounds() -> None:
    root = make_repo()
    (root / "docs/oversize.txt").write_bytes(b"x" * (ctx.MAX_SLICE_FILE_BYTES + 1))
    git(root, "add", "docs/oversize.txt")
    git(root, "commit", "-m", "add oversize fixture")
    oversize = run_cli(root, "--task", "oversize", "--slice-path", "docs/oversize.txt")
    if oversize.returncode == 0 or "slice file exceeds size bound" not in oversize.stderr:
        fail(f"oversize slice was not blocked: {oversize.stderr}")

    (root / "docs/long.txt").write_text(
        "target " + ("x" * (ctx.MAX_SLICE_LINE_BYTES + 5)) + "\n",
        encoding="utf-8",
    )
    git(root, "add", "docs/long.txt")
    git(root, "commit", "-m", "add long-line fixture")
    long_line = run_cli(root, "--task", "target", "--slice-path", "docs/long.txt")
    if long_line.returncode == 0 or "selected slice line exceeds size bound" not in long_line.stderr:
        fail(f"overlong selected line was not blocked: {long_line.stderr}")

    too_many = run_cli(
        root,
        "--task", "target",
        "--slice-path", "tools/large_context.py",
        "--max-slice-lines", str(ctx.MAX_SLICE_LINES + 1),
    )
    if too_many.returncode == 0:
        fail("out-of-bound max slice lines was accepted")
    too_wide = run_cli(
        root,
        "--task", "target",
        "--slice-path", "tools/large_context.py",
        "--slice-context", str(ctx.MAX_SLICE_CONTEXT + 1),
    )
    if too_wide.returncode == 0:
        fail("out-of-bound slice context was accepted")


def test_slice_oversize_preflights_before_blob_read() -> None:
    root = make_repo()
    (root / "docs/oversize-preflight.txt").write_bytes(
        b"x" * (ctx.MAX_SLICE_FILE_BYTES + 1)
    )
    git(root, "add", "docs/oversize-preflight.txt")
    git(root, "commit", "-m", "add oversize preflight fixture")
    original = ctx.run_git_bytes

    def guarded(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        if (args and args[0] == "show") or args[:2] == ("cat-file", "blob"):
            fail("oversized blob content was read before the size gate")
        return original(repo, *args)

    ctx.run_git_bytes = guarded
    try:
        try:
            ctx.slice_context(
                root,
                "oversize preflight",
                "docs/oversize-preflight.txt",
                5,
                1,
            )
        except SystemExit as exc:
            if "slice file exceeds size bound" not in str(exc):
                fail(f"wrong oversize preflight failure: {exc}")
        else:
            fail("oversized blob passed the preflight size gate")
    finally:
        ctx.run_git_bytes = original


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


def test_adopted_helper_runs_without_pyyaml_site_packages() -> None:
    root = make_repo()
    (root / ".engineering/tests.yaml").write_text(
        """version: 1

paths:
  "tools/**":
    domains:
      - workflows
      - session-continuity
""",
        encoding="utf-8",
    )
    git(root, "add", ".engineering/tests.yaml")
    git(root, "commit", "-m", "add tests manifest")
    run = run_cli_without_site(root, "--task", "context routing", "--max-orientation", "2")
    if run.returncode != 0:
        fail(f"stdlib-only adopted helper failed: {run.stdout} {run.stderr}")
    if "CONTEXT_ROUTER=PASS" not in run.stdout:
        fail(f"stdlib-only helper did not complete: {run.stdout}")
    paths = parse_orientation_paths(run.stdout)
    if "tools/context_router.py" not in paths:
        fail(f"stdlib-only task path relevance was lost: {paths}")

    (root / "tools/context_router.py").write_text("changed\n", encoding="utf-8")
    legacy = run_cli_without_site(root)
    if legacy.returncode != 0:
        fail(f"stdlib-only legacy routing failed: {legacy.stdout} {legacy.stderr}")
    if "AFFECTED_DOMAINS=session-continuity,workflows" not in legacy.stdout:
        fail(f"stdlib-only tests.yaml fallback lost domains: {legacy.stdout}")

def test_changed_file_output_is_json_encoded() -> None:
    root = make_repo()
    hostile = "evil\nAFFECTED_DOMAINS=fake"
    (root / hostile).write_text("x\n", encoding="utf-8")
    run = run_cli(root)
    if run.returncode != 0:
        fail(f"hostile path routing failed: {run.stdout} {run.stderr}")
    encoded = "CHANGED_FILE_JSON=" + json.dumps(hostile, ensure_ascii=True)
    if encoded not in run.stdout:
        fail(f"changed path was not JSON encoded: {run.stdout}")
    if "\nAFFECTED_DOMAINS=fake\n" in run.stdout:
        fail("changed path forged a line-protocol record")


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
        test_slice_is_exact_head_deterministic_and_bounded,
        test_slice_rejects_head_change_during_read,
        test_slice_no_match_and_unicode,
        test_slice_fail_closed_boundaries,
        test_slice_size_and_line_bounds,
        test_slice_oversize_preflights_before_blob_read,
        test_dirty_worktree_fails_closed,
        test_orientation_rejects_head_change_during_scan,
        test_adopted_helper_runs_without_pyyaml_site_packages,
        test_changed_file_output_is_json_encoded,
        test_legacy_router_output_is_unchanged_without_task,
        test_invalid_orientation_inputs_fail_closed,
    ]
    for test in tests:
        test()
    print("ENGINEERING_CONTEXT_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
