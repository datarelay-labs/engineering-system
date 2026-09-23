#!/usr/bin/env python3
"""Deterministic regressions for token-efficient context and test routing."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "tools" / "engineering-context.py"
TEST = ROOT / "tools" / "engineering-test.py"


def run(*args: str, cwd: Path | None = None, check: bool = True):
    return subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
    )


def git(cwd: Path, *args: str):
    return run("git", *args, cwd=cwd)


def commit(cwd: Path, message: str):
    git(cwd, "add", "-A")
    git(cwd, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", message)


def fixture() -> Path:
    base = Path(tempfile.mkdtemp())
    repo = base / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / ".engineering").mkdir()
    (repo / "src").mkdir()
    manifest = {
        "version": 1,
        "paths": {
            "src/**": {"domains": ["core"]},
            ".engineering/**": {"domains": ["config"]},
        },
        "scenarios": [
            {
                "id": "EXPENSIVE-FIRST",
                "name": "full integration",
                "level": "integration",
                "domains": ["core", "config"],
                "command": "python3 -c \"print('expensive')\"",
                "agent_default": True,
            },
            {
                "id": "CHEAP-SECOND",
                "name": "cheap static",
                "level": "static",
                "domains": ["core"],
                "command": "python3 -c \"print('cheap')\"",
                "cost": "cheap",
                "estimated_seconds": 1,
                "timeout_seconds": 30,
                "agent_default": True,
                "scope": "static",
            },
        ],
    }
    (repo / ".engineering" / "tests.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    (repo / "src" / "demo.py").write_text("VALUE = 1\n", encoding="utf-8")
    commit(repo, "base")
    git(repo, "checkout", "-b", "feature")
    (repo / "src" / "demo.py").write_text("VALUE = 2\n", encoding="utf-8")
    commit(repo, "change")
    return repo


def test_diff_first_context_and_cheapest_selection():
    repo = fixture()
    context = run(sys.executable, str(CONTEXT), "--root", str(repo), "--base", "main")
    assert "CHANGED_FILE=src/demo.py" in context.stdout
    assert "AFFECTED_DOMAINS=core" in context.stdout
    selected = run(sys.executable, str(TEST), "--root", str(repo), "--base", "main")
    assert "TEST_SELECTED=CHEAP-SECOND" in selected.stdout
    assert "TEST_COST=cheap" in selected.stdout
    assert "EXPENSIVE-FIRST" not in selected.stdout
    executed = run(sys.executable, str(TEST), "--root", str(repo), "--base", "main", "--run")
    assert "TEST_RESULT=PASS" in executed.stdout
    assert (repo / ".engineering" / "agent-logs" / "CHEAP-SECOND.log").read_text(encoding="utf-8").strip() == "cheap"


def test_expensive_metadata_only_is_not_auto_run():
    repo = fixture()
    git(repo, "checkout", "main")
    manifest = yaml.safe_load((repo / ".engineering" / "tests.yaml").read_text(encoding="utf-8"))
    manifest["scenarios"] = [
        {
            "id": "ONLY-EXPENSIVE",
            "name": "integration",
            "level": "integration",
            "domains": ["config"],
            "command": "python3 -c \"raise SystemExit(99)\"",
        }
    ]
    (repo / ".engineering" / "tests.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    commit(repo, "manifest baseline")
    git(repo, "checkout", "-b", "metadata")
    (repo / ".engineering" / "project.yaml").write_text("engineering_system: {}\n", encoding="utf-8")
    commit(repo, "metadata only")
    result = run(sys.executable, str(TEST), "--root", str(repo), "--base", "main", "--run")
    assert "TEST_SELECTED=ONLY-EXPENSIVE" in result.stdout
    assert "TEST_SELECTION=SKIP_EXPENSIVE_METADATA_ONLY" in result.stdout
    assert "TEST_RESULT=" not in result.stdout


def test_explicit_unresolved_base_fails_closed():
    repo = fixture()
    for tool, success_marker in (
        (CONTEXT, "CONTEXT_ROUTER=PASS"),
        (TEST, "TEST_SELECTION=PLAN"),
    ):
        result = run(sys.executable, str(tool), "--root", str(repo), "--base", "does-not-exist", check=False)
        assert result.returncode != 0
        assert "unresolved base: does-not-exist" in result.stdout
        assert success_marker not in result.stdout
        assert "TEST_RESULT=PASS" not in result.stdout


def test_explicit_base_without_merge_base_fails_closed():
    repo = fixture()
    git(repo, "checkout", "--orphan", "unrelated")
    (repo / "other.txt").write_text("unrelated\n", encoding="utf-8")
    commit(repo, "unrelated")
    git(repo, "checkout", "feature")
    for tool, success_marker in (
        (CONTEXT, "CONTEXT_ROUTER=PASS"),
        (TEST, "TEST_SELECTION=PLAN"),
    ):
        result = run(sys.executable, str(tool), "--root", str(repo), "--base", "unrelated", check=False)
        assert result.returncode != 0
        assert "git diff failed for base: unrelated" in result.stdout
        assert success_marker not in result.stdout
        assert "TEST_RESULT=PASS" not in result.stdout


def test_dirty_worktree_is_unioned_with_committed_diff():
    repo = fixture()
    git(repo, "mv", "src/demo.py", "src/renamed.py")
    (repo / "src" / "untracked.py").write_text("VALUE = 3\n", encoding="utf-8")
    (repo / ".engineering" / "notes.yaml").write_text("x: 1\n", encoding="utf-8")
    context = run(sys.executable, str(CONTEXT), "--root", str(repo), "--base", "main")
    for path in ("src/demo.py", "src/renamed.py", "src/untracked.py", ".engineering/notes.yaml"):
        assert f"CHANGED_FILE={path}" in context.stdout
    assert "AFFECTED_DOMAINS=config,core" in context.stdout
    selected = run(sys.executable, str(TEST), "--root", str(repo), "--base", "main")
    assert "TEST_DOMAINS=config,core" in selected.stdout
    assert "TEST_SELECTION=PLAN" in selected.stdout


def main() -> int:
    test_diff_first_context_and_cheapest_selection()
    test_expensive_metadata_only_is_not_auto_run()
    test_explicit_unresolved_base_fails_closed()
    test_explicit_base_without_merge_base_fails_closed()
    test_dirty_worktree_is_unioned_with_committed_diff()
    print("TOKEN_EFFICIENCY_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
