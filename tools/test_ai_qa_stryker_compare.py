#!/usr/bin/env python3
"""Adversarial tests for pinned, same-mutant Stryker comparison."""
from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

tool = Path(__file__).with_name("ai_qa_stryker_compare.py")
spec = importlib.util.spec_from_file_location("ai_qa_stryker_compare", tool)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

PATH = "src/guided/normalizationValidation.ts"
HEAD = "a" * 40
DIGEST = "b" * 64


def mutant(id: str, status: str, replacement: str = "false") -> dict:
    return {
        "id": id, "mutatorName": "ConditionalExpression", "replacement": replacement,
        "location": {"start": {"line": 35, "column": 1}, "end": {"line": 36, "column": 20}},
        "status": status,
    }


def report(*items: dict) -> dict:
    return {"schemaVersion": "2", "files": {PATH: {"mutants": list(items)}}}


class ScoreTest(unittest.TestCase):
    def compare(self, before: dict, after: dict, **overrides) -> dict:
        return module.compare(before, after, head=overrides.get("head", HEAD),
                              source_sha256=overrides.get("source_sha256", DIGEST),
                              report_hashes=("c" * 64, "d" * 64))

    def test_real_additional_kills_are_measured_once(self):
        existing = report(mutant("0", "Killed"), mutant("1", "Survived"),
                          mutant("2", "NoCoverage"))
        improved = report(mutant("0", "Killed"), mutant("1", "Killed"),
                          mutant("2", "NoCoverage"))
        result = self.compare(existing, improved)
        self.assertEqual(result["attempted"], 3)
        self.assertEqual(result["qualified"], 3)
        self.assertEqual(result["original_killed"], 1)
        self.assertEqual(result["enhanced_killed"], 2)
        self.assertEqual(result["independent_additional_kills"], 1)
        self.assertEqual(result["original_mutation_score"], 0.3333)
        self.assertEqual(result["enhanced_mutation_score"], 0.6667)
        self.assertTrue(result["same_mutant_set"])
        self.assertFalse(result["product_user_e2e_pass"])
        self.assertEqual(result["authority"], "T0_ADVISORY_ONLY")

    def test_timeout_and_compile_error_never_count_as_kills(self):
        before = report(mutant("0", "Survived"), mutant("1", "TimedOut"),
                        mutant("2", "CompileError"))
        after = report(mutant("0", "Killed"), mutant("1", "Killed"),
                       mutant("2", "Killed"))
        out = self.compare(before, after)
        self.assertEqual(out["qualified"], 1)
        self.assertEqual(out["not_qualified"], 2)
        self.assertEqual(out["independent_additional_kills"], 1)
        self.assertEqual(out["original_status_counts"]["TimedOut"], 1)
        self.assertEqual(out["original_status_counts"]["CompileError"], 1)

    def test_missing_or_changed_mutant_set_denied(self):
        before = report(mutant("0", "Survived"), mutant("1", "Killed"))
        changed = report(mutant("0", "Killed"))
        with self.assertRaisesRegex(module.Invalid, "DIFFERENT_MUTANT_SET"):
            self.compare(before, changed)
        changed = report(mutant("0", "Killed"), mutant("1", "Killed", "true"))
        with self.assertRaisesRegex(module.Invalid, "DIFFERENT_MUTANT_SET"):
            self.compare(before, changed)

    def test_duplicate_mutants_denied(self):
        duplicated = report(mutant("0", "Killed"), copy.deepcopy(mutant("0", "Survived")))
        with self.assertRaisesRegex(module.Invalid, "DUPLICATE_MUTANT"):
            self.compare(duplicated, duplicated)

    def test_regression_killed_to_survived_denied(self):
        with self.assertRaisesRegex(module.Invalid, "REGRESSION_KILLED_MUTANT_SURVIVED"):
            self.compare(report(mutant("0", "Killed")), report(mutant("0", "Survived")))

    def test_no_qualified_mutants_denied(self):
        with self.assertRaisesRegex(module.Invalid, "NO_QUALIFIED_MUTANTS"):
            self.compare(report(mutant("0", "TimedOut")),
                         report(mutant("0", "CompileError")))

    def test_wrong_source_and_invalid_status_denied(self):
        with self.assertRaisesRegex(module.Invalid, "INVALID_SOURCE_PROVENANCE"):
            self.compare(report(mutant("0", "Killed")), report(mutant("0", "Killed")),
                         head="not-a-git-head")
        with self.assertRaisesRegex(module.Invalid, "INVALID_STATUS"):
            self.compare(report(mutant("0", "FABRICATED_PASS")),
                         report(mutant("0", "Killed")))

    def test_malformed_record_denied(self):
        with self.assertRaisesRegex(module.Invalid, "INVALID_MUTANT"):
            self.compare(report(None), report(None))
        with self.assertRaisesRegex(module.Invalid, "MISSING_MUTANT_IDENTITY"):
            self.compare(report({"id": "0", "status": "Killed"}),
                         report({"id": "0", "status": "Killed"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
