#!/usr/bin/env python3
"""Regression tests for deterministic Engineering System adoption bootstrap."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADOPT = ROOT / "tools" / "adopt.py"
CHECK = ROOT / "tools" / "check-adoption.py"
BASELINE = "a" * 40


def run(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
    )


def init_repo(root: Path) -> None:
    run("git", "init", "-q", str(root))
    run("git", "config", "user.email", "test@example.invalid", cwd=root)
    run("git", "config", "user.name", "Adoption Test", cwd=root)


def commit_all(root: Path, message: str = "fixture") -> None:
    run("git", "add", ".", cwd=root)
    run("git", "commit", "-qm", message, cwd=root)


def test_clean_python_bootstrap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-python"
        target.mkdir()
        init_repo(target)
        (target / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
        (target / "tests").mkdir()
        (target / "tests" / "test_demo.py").write_text("def test_demo():\n    assert True\n", encoding="utf-8")
        commit_all(target)

        audit = run(sys.executable, str(ADOPT), "--root", str(target), "--audit")
        assert "PROJECT_TYPE=python" in audit.stdout
        assert "python -m pytest -q" in audit.stdout

        applied = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "python -m pytest -q",
        )
        assert "ADOPTION_BOOTSTRAP=PASS" in applied.stdout

        required = (
            "AGENTS.md",
            ".engineering/project.yaml",
            ".engineering/tests.yaml",
            ".engineering/release.yaml",
            ".cursor/rules/engineering-system.mdc",
            ".cursor/commands/resume.md",
            ".github/ISSUE_TEMPLATE/ai-work-packet.md",
            ".github/workflows/engineering-system.yml",
        )
        for rel in required:
            assert (target / rel).is_file(), rel

        project_text = (target / ".engineering/project.yaml").read_text(encoding="utf-8")
        assert 'version: "1.4.0"' in project_text
        assert "mode: adopted" in project_text
        assert BASELINE in project_text

        workflow_text = (target / ".github/workflows/engineering-system.yml").read_text(encoding="utf-8")
        assert f"adoption-compliance.yml@{BASELINE}" in workflow_text
        assert f"affected-tests.yml@{BASELINE}" in workflow_text

        checked = run(sys.executable, str(CHECK), "--root", str(target))
        assert "ENGINEERING_SYSTEM_ADOPTION=PASS" in checked.stdout


def test_rule_review_is_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo-rules"
        target.mkdir()
        init_repo(target)
        (target / "CLAUDE.md").write_text("# Existing project rule\n", encoding="utf-8")
        (target / "go.mod").write_text("module example.invalid/demo\n\ngo 1.23\n", encoding="utf-8")
        commit_all(target)

        result = run(
            sys.executable,
            str(ADOPT),
            "--root",
            str(target),
            "--apply",
            "--baseline-sha",
            BASELINE,
            "--test-command",
            "go test ./...",
            check=False,
        )
        assert result.returncode != 0
        assert "RULE_REVIEW_REQUIRED=CLAUDE.md" in result.stdout
        assert "classify existing rules before apply" in result.stdout


def main() -> int:
    run(sys.executable, "-m", "py_compile", str(ADOPT), str(CHECK))
    test_clean_python_bootstrap()
    test_rule_review_is_fail_closed()
    print("ADOPTION_TOOL_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
