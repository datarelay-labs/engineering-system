#!/usr/bin/env python3
"""Regression tests for organization-wide Engineering System rollout."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ADOPT = ROOT / "tools" / "adopt.py"
UPGRADE = ROOT / "tools" / "upgrade-adoption.py"
ROLLOUT = ROOT / "tools" / "org-rollout.py"
BASELINE = "a" * 40
NEW_BASELINE = "b" * 40


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
    run("git", "config", "user.name", "Rollout Test", cwd=root)
    run("git", "branch", "-M", "main", cwd=root)


def commit_all(root: Path, message: str = "fixture") -> None:
    run("git", "add", ".", cwd=root)
    run("git", "commit", "-qm", message, cwd=root)


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def adopt_python(target: Path, baseline: str = BASELINE) -> None:
    (target / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (target / "tests").mkdir(exist_ok=True)
    (target / "tests" / "test_demo.py").write_text("def test_demo():\n    assert True\n", encoding="utf-8")
    commit_all(target, "product fixture")
    run(
        sys.executable,
        str(ADOPT),
        "--root",
        str(target),
        "--apply",
        "--baseline-sha",
        baseline,
        "--test-command",
        "python -m pytest -q",
    )
    commit_all(target, "adopt engineering system")


def write_inventory(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def test_org_rollout_matrix() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        current = base / "current"
        outdated = base / "outdated"
        unadopted = base / "unadopted"
        archived = base / "archived"
        incomplete = base / "incomplete"

        for path in (current, outdated, unadopted, archived, incomplete):
            path.mkdir()
            init_repo(path)

        adopt_python(current, BASELINE)
        adopt_python(outdated, BASELINE)
        project_path = outdated / ".engineering" / "project.yaml"
        project = load_yaml(project_path)
        project["engineering_system"]["version"] = "1.6.0"
        project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        workflow_path = outdated / ".github" / "workflows" / "engineering-system.yml"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        workflow_path.write_text(workflow_text.replace(BASELINE, BASELINE), encoding="utf-8")
        # Keep managed workflow identity while marking metadata outdated.
        commit_all(outdated, "mark adoption outdated")

        (unadopted / "README.md").write_text("unadopted\n", encoding="utf-8")
        commit_all(unadopted, "unadopted fixture")

        adopt_python(archived, BASELINE)
        (incomplete / ".engineering").mkdir()
        (incomplete / ".cursor" / "rules").mkdir(parents=True)
        (incomplete / ".cursor" / "rules" / "engineering-system.mdc").write_text("x\n", encoding="utf-8")
        commit_all(incomplete, "incomplete adoption markers")

        inventory = base / "inventory.json"
        write_inventory(
            inventory,
            [
                {
                    "full_name": "demo/current",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(current),
                },
                {
                    "full_name": "demo/outdated",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(outdated),
                },
                {
                    "full_name": "demo/unadopted",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(unadopted),
                },
                {
                    "full_name": "demo/archived",
                    "archived": True,
                    "default_branch": "main",
                    "local_path": str(archived),
                },
                {
                    "full_name": "demo/incomplete",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(incomplete),
                },
            ],
        )

        audit = run(sys.executable, str(ROLLOUT), "--inventory-file", str(inventory), "--audit")
        assert "ORG_ROLLOUT_MODE=AUDIT" in audit.stdout
        assert "REPO=demo/current" in audit.stdout and "STATE=CURRENT" in audit.stdout
        assert "REPO=demo/outdated" in audit.stdout and "STATE=OUTDATED" in audit.stdout
        assert "ACTION=UPGRADE" in audit.stdout
        assert "REPO=demo/unadopted" in audit.stdout and "STATE=UNADOPTED" in audit.stdout
        assert "REPO=demo/archived" in audit.stdout and "ACTION=SKIP_ARCHIVED" in audit.stdout
        assert "REPO=demo/incomplete" in audit.stdout and "STATE=INCOMPLETE" in audit.stdout
        assert "ORG_ROLLOUT=PARTIAL" in audit.stdout or "ORG_ROLLOUT=PASS" in audit.stdout

        # Failure path: custom workflow blocks managed upgrade.
        broken = base / "broken"
        broken.mkdir()
        init_repo(broken)
        adopt_python(broken, BASELINE)
        project = load_yaml(broken / ".engineering" / "project.yaml")
        project["engineering_system"]["version"] = "1.5.0"
        (broken / ".engineering" / "project.yaml").write_text(
            yaml.safe_dump(project, sort_keys=False),
            encoding="utf-8",
        )
        workflow = broken / ".github" / "workflows" / "engineering-system.yml"
        workflow.write_text(workflow.read_text(encoding="utf-8") + "\n# local customization\n", encoding="utf-8")
        commit_all(broken, "break managed workflow")

        fail_inventory = base / "fail-inventory.json"
        write_inventory(
            fail_inventory,
            [
                {
                    "full_name": "demo/broken",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(broken),
                }
            ],
        )
        failed = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(fail_inventory),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert failed.returncode != 0
        assert "OUTCOME=FAIL" in failed.stdout
        assert "ORG_ROLLOUT=FAIL" in failed.stdout

        applied = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(inventory),
            "--apply",
            "--baseline-sha",
            NEW_BASELINE,
            check=False,
        )
        assert "REPO=demo/outdated" in applied.stdout
        assert "OUTCOME=APPLIED" in applied.stdout
        assert "REPO=demo/unadopted" in applied.stdout
        assert "OUTCOME=NEEDS_INPUT" in applied.stdout
        assert "ORG_ROLLOUT=PARTIAL" in applied.stdout
        upgraded_project = load_yaml(outdated / ".engineering" / "project.yaml")
        assert upgraded_project["engineering_system"]["version"] == "1.6.1"
        assert upgraded_project["engineering_system"]["baseline"] == NEW_BASELINE
        branch = run("git", "branch", "--show-current", cwd=outdated)
        assert branch.stdout.strip().startswith("chore/engineering-system-rollout-")

        # Idempotent rerun against the upgraded checkout reports CURRENT/NO_CHANGE.
        rerun_inventory = base / "rerun-inventory.json"
        write_inventory(
            rerun_inventory,
            [
                {
                    "full_name": "demo/outdated",
                    "archived": False,
                    "default_branch": "main",
                    "local_path": str(outdated),
                }
            ],
        )
        # Return to main content by checking out the rollout branch remains upgraded;
        # classify against current metadata should already be CURRENT.
        rerun = run(
            sys.executable,
            str(ROLLOUT),
            "--inventory-file",
            str(rerun_inventory),
            "--audit",
        )
        assert "STATE=CURRENT" in rerun.stdout
        assert "OUTCOME=NO_CHANGE" in rerun.stdout
        assert "ORG_ROLLOUT=PASS" in rerun.stdout


def main() -> int:
    run(sys.executable, "-m", "py_compile", str(ROLLOUT), str(ADOPT), str(UPGRADE))
    test_org_rollout_matrix()
    print("ORG_ROLLOUT_TOOL_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
