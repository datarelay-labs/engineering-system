#!/usr/bin/env python3
"""Deterministic, no-product runtime unit test of isolated source mutation scorer."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "adapters/ai-qa/parser-studio-product-mutation.py"
spec = importlib.util.spec_from_file_location("product_mutation", PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ScoreTest(unittest.TestCase):
    def test_distinguishes_preexisting_and_holdout_kills(self):
        rows = [
            {"id": "existing", "original": "FAIL", "with_holdout": "FAIL"},
            {"id": "holdout", "original": "PASS", "with_holdout": "FAIL"},
            {"id": "survivor", "original": "PASS", "with_holdout": "PASS"},
        ]
        actual = module.score(rows, "PASS", "PASS")
        self.assertEqual(actual["valid_mutants"], 3)
        self.assertEqual(actual["caught_existing_tests"], 1)
        self.assertEqual(actual["additional_caught_by_independent_holdout"], 1)
        self.assertEqual(actual["survived_both"], 1)
        self.assertEqual(actual["existing_mutation_score"], 0.3333)
        self.assertEqual(actual["combined_mutation_score"], 0.6667)

    def test_inconsistent_suite_outcome_not_miscounted(self):
        with self.assertRaisesRegex(module.Unsafe, "INCONSISTENT_TEST_OUTCOME"):
            module.score([{"id": "flaky", "original": "FAIL", "with_holdout": "PASS"}],
                         "PASS", "PASS")

    def test_invalid_mutant_not_counted(self):
        actual = module.score([{"id": "syntax-error", "original": "INVALID_MUTANT",
                                "with_holdout": "INVALID_MUTANT"}], "PASS", "PASS")
        self.assertEqual(actual["not_qualified"], 1)
        self.assertEqual(actual["caught_existing_tests"], 0)
        self.assertEqual(actual["additional_caught_by_independent_holdout"], 0)

    def test_invalid_or_timeout_not_in_kills(self):
        rows = [
            {"id": "unknown", "original": "INFRA_FAILURE", "with_holdout": "FAIL"},
            {"id": "timeout", "original": "TIMEOUT", "with_holdout": "TIMEOUT"},
        ]
        result = module.score(rows, "PASS", "PASS")
        self.assertEqual(result["valid_mutants"], 0)
        self.assertEqual(result["not_qualified"], 2)
        self.assertIsNone(result["combined_mutation_score"])

    def test_blocks_red_baseline(self):
        with self.assertRaisesRegex(module.Unsafe, "BASELINE_NOT_GREEN"):
            module.score([], "FAIL", "PASS")

    def test_bad_head_fails_before_file_access(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(module.Unsafe, "BAD_EXPECTED_HEAD"):
                module.run(Path(t), "not-head", output=Path(t) / "report.json")

    def test_outfile_never_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "exists.json"
            path.write_text("PRESERVE")
            with self.assertRaisesRegex(module.Unsafe, "REFUSE_OVERWRITE_EXISTING_EVIDENCE"):
                module.run(Path(t), "a" * 40, output=path)
            self.assertEqual(path.read_text(), "PRESERVE")

    def test_rejects_wrong_source_identity(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(module.Unsafe, "INVALID_PRODUCT_CHECKOUT"):
                module.obtain_snapshot(Path(t), "a" * 40)

    def test_pinned_holdout_contains_actual_nested_case(self):
        raw = module.HOLDOUT_SOURCE.read_text(encoding="utf-8")
        self.assertIn("custom.actor", raw)
        self.assertIn("custom.actor.id", raw)
        self.assertIn("TARGET_COLLISION", raw)
        self.assertIn("toHaveLength(2)", raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
