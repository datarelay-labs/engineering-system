#!/usr/bin/env python3
"""Adversarial QA benchmark regressions: exact-head, blind oracle, artifact integrity."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).with_name("ai_qa_benchmark.py")
HEAD = "a" * 40
OTHER_HEAD = "b" * 40


class BenchmarkTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="ai-qa-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.artifacts = self.root / "proof"
        self.artifacts.mkdir()
        self.plan_path = self.root / "plan.json"
        self.report_path = self.root / "observations.json"
        self.plan = {
            "schema_version": 1,
            "kind": "ai-qa-holdout",
            "target_repo": "datarelay-labs/engineering-system",
            "subject_head": HEAD,
            "intent_revision": 1,
            "plan_id": "pilot-001",
            "journeys": [
                {"id": "upload", "required_checks": ["public_action", "final_outcome", "independent_oracle"]},
                {"id": "inspect", "required_checks": ["public_action", "final_outcome", "independent_oracle"]},
            ],
            "faults": [
                {"id": "fault-critical", "journey_id": "upload", "severity": "CRITICAL"},
            ],
        }
        self.report = {
            "schema_version": 1,
            "kind": "ai-qa-observation",
            "target_repo": self.plan["target_repo"],
            "subject_head": HEAD,
            "intent_revision": 1,
            "plan_id": self.plan["plan_id"],
            "developer_actor": {"identity": "coder", "context_id": "implementation-context"},
            "qa_actor": {"identity": "blind-qa", "context_id": "separate-context"},
            "journeys": [
                self.journey("upload", "FAIL", {"public_action": "PASS", "final_outcome": "FAIL",
                                                "independent_oracle": "FAIL"}),
                self.journey("inspect", "PASS", {"public_action": "PASS", "final_outcome": "PASS",
                                                 "independent_oracle": "PASS"}),
            ],
            "findings": [
                {"id": "finding-001", "fault_id": "fault-critical", "journey_id": "upload",
                 "artifact": self.artifact("fault-critical-proof")},
            ],
        }

    def artifact(self, name: str) -> dict:
        raw = f"reproducible artifact: {name}\n".encode()
        (self.artifacts / name).write_bytes(raw)
        return {"path": name, "sha256": hashlib.sha256(raw).hexdigest()}

    def journey(self, jid: str, status: str, states: dict[str, str]) -> dict:
        return {
            "id": jid, "status": status,
            "checks": [
                {"id": name, "status": state, "artifact": self.artifact(jid + "-" + name)}
                for name, state in states.items()
            ],
        }

    def evaluate(self, *, expected_head: str = HEAD, pin: str | None = None) -> tuple[int, dict]:
        plan_raw = (json.dumps(self.plan, sort_keys=True) + "\n").encode()
        self.plan_path.write_bytes(plan_raw)
        self.report_path.write_text(json.dumps(self.report) + "\n", encoding="utf-8")
        args = [sys.executable, str(TOOL), "--plan", str(self.plan_path),
                "--report", str(self.report_path), "--artifacts-root", str(self.artifacts),
                "--expected-head", expected_head,
                "--expected-plan-sha256", pin or hashlib.sha256(plan_raw).hexdigest()]
        ran = subprocess.run(args, text=True, capture_output=True, check=False, timeout=12)
        self.assertFalse(ran.stderr, ran.stderr)
        return ran.returncode, json.loads(ran.stdout)

    def assert_block(self, reason: str = "") -> None:
        rc, out = self.evaluate()
        self.assertEqual((rc, out["assessment"]), (3, "BLOCK"), out)
        if reason:
            self.assertIn(reason, out["reasons"])
        self.assertEqual(out["authority"], "T0_ADVISORY_ONLY")
        self.assertFalse(out["release_authorized"])

    def test_valid_pinned_blind_benchmark(self):
        rc, out = self.evaluate()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["assessment"], "BENCHMARK_COMPLETE")
        self.assertEqual(out["metrics"]["fault_recall"], 1.0)
        self.assertEqual(out["metrics"]["coverage_fraction"], 1.0)
        self.assertFalse(out["human_equivalent_e2e_verified"])

    def test_explicit_head_mismatch(self):
        rc, out = self.evaluate(expected_head=OTHER_HEAD)
        self.assertEqual((rc, out["assessment"]), (3, "BLOCK"))
        self.assertIn("STALE_OR_UNEXPECTED_HEAD", out["reasons"])

    def test_report_head_mismatch(self):
        self.report["subject_head"] = OTHER_HEAD
        self.assert_block("SUBJECT_MISMATCH")

    def test_plan_tamper_detected_against_prior_digest(self):
        raw = (json.dumps(self.plan, sort_keys=True) + "\n").encode()
        frozen = hashlib.sha256(raw).hexdigest()
        self.plan["faults"][0]["severity"] = "LOW"
        rc, out = self.evaluate(pin=frozen)
        self.assertEqual((rc, out["assessment"]), (3, "BLOCK"))
        self.assertIn("UNPINNED_OR_CHANGED_ORACLE", out["reasons"])

    def test_developer_and_qa_context_separation(self):
        self.report["qa_actor"]["context_id"] = "implementation-context"
        self.assert_block("DECLARED_CONTEXT_COLLISION")

    def test_developer_and_qa_identity_separation(self):
        self.report["qa_actor"]["identity"] = "coder"
        self.assert_block("DECLARED_ACTOR_COLLISION")

    def test_false_pass_is_quality_failure(self):
        self.report["journeys"][0]["status"] = "PASS"
        for c in self.report["journeys"][0]["checks"]:
            c["status"] = "PASS"
        self.report["findings"] = []
        rc, out = self.evaluate()
        self.assertEqual((rc, out["assessment"]), (2, "QUALITY_FAIL"))
        self.assertEqual(out["metrics"]["missed_high_critical"], 1)
        self.assertEqual(out["metrics"]["false_pass_journeys"], 1)

    def test_missing_seeded_critical_fault(self):
        self.report["findings"] = []
        rc, out = self.evaluate()
        self.assertEqual((rc, out["assessment"]), (2, "QUALITY_FAIL"))
        self.assertIn("fault-critical", out["findings"]["missed_fault_ids"])

    def test_unmatched_alert_not_assumed_true_positive(self):
        self.report["findings"][0]["fault_id"] = "invented-fault"
        rc, out = self.evaluate()
        self.assertEqual((rc, out["assessment"]), (2, "QUALITY_FAIL"))
        self.assertEqual(out["metrics"]["unmatched_findings"], 1)
        self.assertIn("UNMATCHED_FINDINGS_NEED_TRIAGE", out["reasons"])

    def test_missing_journey_unverified(self):
        self.report["journeys"].pop()
        rc, out = self.evaluate()
        self.assertEqual((rc, out["assessment"]), (2, "INCOMPLETE"))
        self.assertIn("MISSING_JOURNEY:inspect", out["reasons"])

    def test_missing_required_check_unverified(self):
        self.report["journeys"][1]["checks"].pop()
        rc, out = self.evaluate()
        self.assertEqual((rc, out["assessment"]), (2, "INCOMPLETE"))
        self.assertEqual(out["metrics"]["coverage_fraction"], 1.0)
        self.assertTrue(any(x.startswith("MISSING_CHECK:") for x in out["reasons"]))

    def test_missing_artifact_unverified(self):
        self.report["journeys"][1]["checks"][0]["artifact"]["path"] = "does-not-exist"
        rc, out = self.evaluate()
        self.assertEqual((rc, out["assessment"]), (2, "INCOMPLETE"))
        self.assertTrue(any(x.startswith("MISSING_CHECK_ARTIFACT:") for x in out["reasons"]))

    def test_altered_artifact_fail_closed(self):
        self.report["journeys"][1]["checks"][0]["artifact"]["sha256"] = "0" * 64
        self.assert_block("ALTERED_ARTIFACT")

    def test_path_traversal_fail_closed(self):
        self.report["journeys"][1]["checks"][0]["artifact"]["path"] = "../outside"
        self.assert_block("UNSAFE_ARTIFACT_PATH")

    def test_absolute_path_fail_closed(self):
        self.report["journeys"][1]["checks"][0]["artifact"]["path"] = "/etc/hosts"
        self.assert_block("UNSAFE_ARTIFACT_PATH")

    def test_symlink_artifact_fail_closed(self):
        (self.artifacts / "unsafe-link").symlink_to(self.artifacts / "inspect-public_action")
        self.report["journeys"][1]["checks"][0]["artifact"]["path"] = "unsafe-link"
        self.assert_block("ARTIFACT_SYMLINK")

    def test_duplicate_claim_fail_closed(self):
        duplicate = copy.deepcopy(self.report["findings"][0])
        duplicate["id"] = "finding-duplicate"
        self.report["findings"].append(duplicate)
        self.assert_block("DUPLICATED_FAULT_CLAIM")

    def test_duplicate_journey_fail_closed(self):
        self.report["journeys"].append(copy.deepcopy(self.report["journeys"][0]))
        self.assert_block("DUPLICATE_ID")

    def test_unknown_check_fail_closed(self):
        self.report["journeys"][1]["checks"].append({
            "id": "unplanned", "status": "PASS", "artifact": self.artifact("unplanned")})
        self.assert_block("UNKNOWN_CHECK")

    def test_fail_without_failed_check_is_not_a_pass(self):
        self.report["journeys"][1]["status"] = "FAIL"
        rc, out = self.evaluate()
        self.assertEqual((rc, out["assessment"]), (2, "QUALITY_FAIL"))
        self.assertIn("FAIL_WITHOUT_FAILED_CHECK:inspect", out["reasons"])

    def test_observation_blocked_is_incomplete(self):
        self.report["journeys"][1]["status"] = "BLOCKED"
        self.report["journeys"][1]["checks"][0]["status"] = "BLOCKED"
        rc, out = self.evaluate()
        self.assertEqual((rc, out["assessment"]), (2, "INCOMPLETE"))
        self.assertIn("NONTERMINAL_JOURNEY:inspect", out["reasons"])

    def test_clean_control_unexpected_failure_needs_triage(self):
        self.report["journeys"][1]["status"] = "FAIL"
        self.report["journeys"][1]["checks"][0]["status"] = "FAIL"
        rc, out = self.evaluate()
        self.assertEqual((rc, out["assessment"]), (2, "QUALITY_FAIL"))
        self.assertIn("UNEXPECTED_CLEAN_FAILURE:inspect", out["reasons"])

    def test_unknown_top_level_keys_denied(self):
        self.report["shell_command"] = "echo malicious"
        self.assert_block("UNKNOWN_FIELDS")

    def test_plan_cannot_omit_independent_oracle(self):
        self.plan["journeys"][0]["required_checks"].remove("independent_oracle")
        self.assert_block("MISSING_INDEPENDENT_ORACLE")

    def test_seeded_fault_unknown_journey_denied(self):
        self.plan["faults"][0]["journey_id"] = "not-planned"
        self.assert_block("INVALID_FAULT")

    def test_wrong_revision_denied(self):
        self.report["intent_revision"] = 2
        self.assert_block("SUBJECT_MISMATCH")

    def test_malformed_status_type_denied_without_traceback(self):
        self.report["journeys"][0]["status"] = []
        self.assert_block("INVALID_JOURNEY_STATUS")

    def test_malformed_check_type_denied_without_traceback(self):
        self.report["journeys"][0]["checks"][0]["status"] = {}
        self.assert_block("INVALID_CHECK_STATUS")

    def test_malformed_fault_severity_denied_without_traceback(self):
        self.plan["faults"][0]["severity"] = []
        self.assert_block("INVALID_FAULT")

    def test_malformed_required_check_denied_without_traceback(self):
        self.plan["journeys"][0]["required_checks"][0] = {}
        self.assert_block("MALFORMED_ID")

    def test_findings_with_missing_artifact_unverified(self):
        self.report["findings"][0]["artifact"]["path"] = "not-produced"
        rc, out = self.evaluate()
        self.assertEqual((rc, out["assessment"]), (2, "INCOMPLETE"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
