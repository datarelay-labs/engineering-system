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
