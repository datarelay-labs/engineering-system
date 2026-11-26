#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from affected_test_selection import (
    SelectionError,
    analyze_impact,
    changed_files,
    run_selected,
    select_scenarios,
)


def manifest():
    return {
        "version": 1,
        "paths": {
            "src/**": {"domains": ["core"]},
            "docs/**": {
                "domains": ["docs"],
                "invalidates": ["SURFACE-PARITY-001"],
            },
            "tests/**": {
                "domains": ["tests"],
                "invalidates": ["ARTIFACT-INTEGRITY-001"],
            },
        },
        "scenarios": [
            {
                "id": "CORE-UNIT-001",
                "name": "core",
                "level": "unit",
                "domains": ["core"],
                "triggers": ["affected"],
                "command": "true",
            },
            {
                "id": "SURFACE-PARITY-001",
                "name": "surface",
                "level": "ux",
                "domains": ["surface"],
                "triggers": ["affected"],
                "command": "true",
            },
            {
                "id": "ARTIFACT-INTEGRITY-001",
                "name": "artifact",
                "level": "static",
                "domains": ["release"],
                "triggers": ["affected"],
                "command": "true",
            },
            {
                "id": "GLOBAL-PR-001",
                "name": "global",
                "level": "static",
                "domains": ["core"],
                "triggers": ["pr"],
                "command": "true",
            },
        ],
    }


class AffectedSelectionTests(unittest.TestCase):
    def test_direct_invalidation_crosses_domain_boundary(self):
        selected, impact = select_scenarios(
            manifest(), ["docs/CLI.md"], "pr", no_base=False
        )
        ids = [item["id"] for item in selected]
        self.assertIn("SURFACE-PARITY-001", ids)
        self.assertIn("GLOBAL-PR-001", ids)
        self.assertNotIn("CORE-UNIT-001", ids)
        self.assertEqual(impact.invalidated_scenarios, frozenset({"SURFACE-PARITY-001"}))

    def test_multiple_invalidations_union_with_domains(self):
        selected, impact = select_scenarios(
            manifest(), ["src/core.py", "tests/test_core.py"], "pr", no_base=False
        )
        ids = [item["id"] for item in selected]
        self.assertIn("CORE-UNIT-001", ids)
        self.assertIn("ARTIFACT-INTEGRITY-001", ids)
        self.assertEqual(impact.affected_domains, frozenset({"core", "tests"}))

    def test_existing_manifest_without_invalidates_remains_compatible(self):
        data = manifest()
        for spec in data["paths"].values():
            spec.pop("invalidates", None)
        selected, impact = select_scenarios(
            data, ["src/core.py"], "pr", no_base=False
        )
        self.assertIn("CORE-UNIT-001", [item["id"] for item in selected])
        self.assertFalse(impact.invalidated_scenarios)

    def test_unknown_invalidation_target_fails_closed(self):
        data = manifest()
        data["paths"]["docs/**"]["invalidates"] = ["MISSING-001"]
        with self.assertRaisesRegex(SelectionError, "unknown scenario"):
            analyze_impact(data, ["docs/CLI.md"])

    def test_invalidation_target_must_be_affected_scenario(self):
        data = manifest()
        data["paths"]["docs/**"]["invalidates"] = ["GLOBAL-PR-001"]
        with self.assertRaisesRegex(SelectionError, "must declare trigger 'affected'"):
            analyze_impact(data, ["docs/CLI.md"])

    def test_matching_rule_with_only_invalidation_is_mapped(self):
        data = manifest()
        data["paths"]["generated/**"] = {
            "domains": [],
            "invalidates": ["ARTIFACT-INTEGRITY-001"],
        }
        impact = analyze_impact(data, ["generated/bundle.sh"])
        self.assertEqual(impact.unmapped_files, ())
        self.assertEqual(
            impact.invalidated_scenarios, frozenset({"ARTIFACT-INTEGRITY-001"})
        )

    def test_committed_deleted_and_renamed_paths_select_both_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root)
            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            (root / "docs").mkdir()
            (root / "src").mkdir()
            (root / "docs" / "obsolete.md").write_text("retire")
            (root / "docs" / "설계 old.md").write_text("move")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD").decode().strip()
            git("rm", "docs/obsolete.md")
            git("mv", "docs/설계 old.md", "src/설계 new.md")
            git("commit", "-qm", "change")
            previous = Path.cwd()
            try:
                os.chdir(root)
                changed, no_base = changed_files(base)
                self.assertFalse(no_base)
                self.assertEqual(set(changed), {
                    "docs/obsolete.md", "docs/설계 old.md", "src/설계 new.md",
                })
                selected, _ = select_scenarios(manifest(), changed, "pr")
                self.assertEqual({item["id"] for item in selected}, {
                    "CORE-UNIT-001", "SURFACE-PARITY-001", "GLOBAL-PR-001",
                })
            finally:
                os.chdir(previous)

    def test_deletion_only_runs_required_check_and_fails_if_behavior_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root)
            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            (root / "docs").mkdir()
            (root / "docs" / "contract.md").write_text("required")
            data = manifest()
            for scenario in data["scenarios"]:
                if scenario["id"] == "SURFACE-PARITY-001":
                    scenario["command"] = "test -f docs/contract.md"
            (root / "tests.yaml").write_text(yaml.safe_dump(data))
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD").decode().strip()
            git("rm", "docs/contract.md")
            git("commit", "-qm", "delete")
            previous = Path.cwd()
            try:
                os.chdir(root)
                with self.assertRaisesRegex(SelectionError, "SURFACE-PARITY-001 exit=1"):
                    run_selected(root / "tests.yaml", base, "pr")
            finally:
                os.chdir(previous)

    def test_workflow_does_not_replace_unavailable_base_with_last_commit(self):
        workflow = Path(__file__).resolve().parents[1] / ".github/workflows/affected-tests.yml"
        steps = yaml.safe_load(workflow.read_text())["jobs"]["affected"]["steps"]
        script = next(step["run"] for step in steps if step.get("id") == "base")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root)
            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            git("commit", "--allow-empty", "-qm", "first")
            base = git("rev-parse", "HEAD").decode().strip()
            git("commit", "--allow-empty", "-qm", "second")
            git("commit", "--allow-empty", "-qm", "third")
            output = root / "github-output"
            def resolve(explicit="", pr="", push=""):
                output.write_text("")
                env = dict(os.environ, INPUT_BASE=explicit, PR_BASE=pr,
                           PUSH_BEFORE=push, GITHUB_OUTPUT=str(output))
                proc = subprocess.run(["bash", "-c", script], cwd=root, env=env,
                                      capture_output=True, text=True, timeout=10)
                return proc.returncode, output.read_text()
            self.assertEqual(resolve(pr=base), (0, f"base={base}\n"))
            rc, emitted = resolve(pr="1" * 40)
            self.assertNotEqual(rc, 0)
            self.assertEqual(emitted, "")
            self.assertEqual(resolve(), (0, "base=\n"))
            self.assertEqual(resolve(push="0" * 40), (0, "base=\n"))

    def test_run_selected_uses_real_git_diff_and_executes_direct_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            (root / "docs").mkdir()
            (root / "docs" / "CLI.md").write_text("v1\n", encoding="utf-8")
            (root / "tests.yaml").write_text(yaml.safe_dump(manifest(), sort_keys=False), encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            (root / "docs" / "CLI.md").write_text("v2\n", encoding="utf-8")
            subprocess.run(["git", "add", "docs/CLI.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "change"], cwd=root, check=True)

            data = manifest()
            for scenario in data["scenarios"]:
                sid = scenario["id"]
                scenario["command"] = f"printf '%s\n' {sid} >> ran.txt"
            (root / "tests.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

            previous = Path.cwd()
            try:
                os.chdir(root)
                self.assertEqual(run_selected(root / "tests.yaml", base, "pr"), 0)
            finally:
                os.chdir(previous)

            ran = (root / "ran.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn("SURFACE-PARITY-001", ran)
            self.assertIn("GLOBAL-PR-001", ran)
            self.assertNotIn("CORE-UNIT-001", ran)


if __name__ == "__main__":
    unittest.main()
