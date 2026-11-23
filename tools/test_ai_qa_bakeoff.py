#!/usr/bin/env python3
"""Pinned, adversarial multi-tool QA comparison: *synthetic* fixtures only."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

p = Path(__file__).with_name("ai_qa_bakeoff.py")
spec = importlib.util.spec_from_file_location("ai_qa_bakeoff", p)
assert spec and spec.loader
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)
HEAD = "a" * 40


def encoded(obj):
    return (json.dumps(obj, sort_keys=True) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class BakeoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qa-compare-synthetic-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = {
            "schema_version": 1, "kind": "ai-qa-holdout",
            "target_repo": "datarelay-labs/engineering-system", "subject_head": HEAD,
            "intent_revision": 1, "plan_id": "synthetic-vendor-compare",
            "journeys": [
                {"id": "broken", "required_checks": ["public_action", "final_outcome", "independent_oracle"]},
                {"id": "clean", "required_checks": ["public_action", "final_outcome", "independent_oracle"]},
            ],
            "faults": [{"id": "seeded-001", "journey_id": "broken", "severity": "CRITICAL"}],
        }
        self.holdout_raw = encoded(self.plan)
        self.holdout_sha = sha(self.holdout_raw)
        self.manifest = {"version": 1, "kind": "ai-qa-provider-bakeoff",
                         "target_repo": self.plan["target_repo"], "subject_head": HEAD,
                         "holdout_sha256": self.holdout_sha, "runs": []}
        for label, success in [("baseline", False), ("ai-qa", True)]:
            artifacts = self.root / label
            artifacts.mkdir()
            def art(s):
                name = s + ".proof"
                raw = ("SYNTHETIC_NO_REAL_PROVIDER_CALL:" + s).encode()
                (artifacts / name).write_bytes(raw)
                return {"path": name, "sha256": sha(raw)}
            def journey(jid, fail):
                return {"id": jid, "status": "FAIL" if fail else "PASS",
                        "checks": [{"id": c, "status": "FAIL" if fail and c != "public_action" else "PASS",
                                    "artifact": art(jid + c)}
                                   for c in ("public_action", "final_outcome", "independent_oracle")]}
            report = {
                "schema_version": 1, "kind": "ai-qa-observation",
                "target_repo": self.plan["target_repo"], "subject_head": HEAD,
                "intent_revision": 1, "plan_id": self.plan["plan_id"],
                "developer_actor": {"identity": "dev", "context_id": "development"},
                "qa_actor": {"identity": label, "context_id": "qa-" + label},
                "journeys": [journey("broken", success), journey("clean", False)],
                "findings": [{"id": "found-seed", "fault_id": "seeded-001", "journey_id": "broken",
                              "artifact": art("finding")}] if success else [],
            }
            report_file = self.root / (label + ".json")
            payload = encoded(report)
            report_file.write_bytes(payload)
            self.manifest["runs"].append({
                "id": label, "tool": label, "report": report_file.name,
                "report_sha256": sha(payload), "artifacts_dir": label,
                "declared_cost_usd": 0.5 if success else 0.1,
                "declared_duration_seconds": 10 if success else 2,
                "declared_invocations": 2,
            })

    def evaluate(self, *, pinned_manifest_sha=None):
        manifest_raw = encoded(self.manifest)
        return qa.evaluate(self.plan, self.manifest, holdout_raw=self.holdout_raw,
                           manifest_raw=manifest_raw, reports_root=self.root,
                           expected_head=HEAD, expected_holdout_sha=self.holdout_sha,
                           expected_manifest_sha=pinned_manifest_sha or sha(manifest_raw))

    def block(self, reason):
        with self.assertRaisesRegex(qa.base.Invalid, reason):
            self.evaluate()

    def test_hidden_common_holdout_comparison(self):
        result = self.evaluate()
        self.assertEqual(result["run_count"], 2)
        self.assertEqual(result["comparison_order_not_a_procurement_decision"], ["ai-qa", "baseline"])
        self.assertEqual(result["results"][0]["detected_faults"], 1)
        self.assertEqual(result["results"][1]["missed_high_critical"], 1)
        self.assertEqual(result["results"][1]["false_pass_journeys"], 1)
        self.assertFalse(result["vendor_execution_verified"])
        self.assertFalse(result["costs_independently_verified"])
        self.assertEqual(result["authority"], "T0_ADVISORY_ONLY")

    def test_holdout_mutation_invalidates_pin(self):
        self.plan["faults"][0]["severity"] = "LOW"
        self.block("UNPINNED_HOLDOUT")

    def test_campaign_mutation_invalidates_external_pin(self):
        original = sha(encoded(self.manifest))
        self.manifest["runs"][0]["declared_cost_usd"] = 99
        with self.assertRaisesRegex(qa.base.Invalid, "UNPINNED_CAMPAIGN"):
            self.evaluate(pinned_manifest_sha=original)

    def test_different_target_head_denied(self):
        self.manifest["subject_head"] = "b" * 40
        self.block("DIFFERENT_CHALLENGE")

    def test_different_holdout_denied(self):
        self.manifest["holdout_sha256"] = "0" * 64
        self.block("DIFFERENT_CHALLENGE")

    def test_duplicate_vendor_label_denied(self):
        self.manifest["runs"][1]["id"] = "baseline"
        self.block("DUPLICATE_INVALID_RUN")

    def test_report_changed_after_freeze(self):
        (self.root / "baseline.json").write_bytes(b'{}')
        self.block("REPORT_CHANGED_AFTER_FREEZE")

    def test_artifact_symlink_denied(self):
        p = self.root / "baseline"
        moved = self.root / "baseline-old"
        p.rename(moved)
        p.symlink_to(moved, target_is_directory=True)
        self.block("UNTRUSTED_ARTIFACT_DIR")

    def test_report_traversal_denied(self):
        self.manifest["runs"][0]["report"] = "../some.json"
        self.block("INVALID_PATH")

    def test_negative_cost_denied(self):
        self.manifest["runs"][0]["declared_cost_usd"] = -1
        self.block("INVALID_COST")

    def test_zero_duration_denied(self):
        self.manifest["runs"][0]["declared_duration_seconds"] = 0
        self.block("INVALID_DURATION")

    def test_boolean_invocation_denied(self):
        self.manifest["runs"][0]["declared_invocations"] = True
        self.block("INVALID_INVOCATIONS")

    def test_untrusted_report_actor_denied(self):
        obj = json.loads((self.root / "baseline.json").read_text())
        obj["qa_actor"]["identity"] = "dev"
        data = encoded(obj)
        (self.root / "baseline.json").write_bytes(data)
        self.manifest["runs"][0]["report_sha256"] = sha(data)
        self.block("DECLARED_ACTOR_COLLISION")

    def test_missing_provider_run_denied(self):
        self.manifest["runs"].pop()
        self.block("INVALID_RUN_COUNT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
