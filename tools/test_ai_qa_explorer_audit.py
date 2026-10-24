#!/usr/bin/env python3
"""Adversarial provenance tests for advisory browser exploration evidence."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

TOOL = Path(__file__).resolve().with_name("ai_qa_explorer_audit.py")
spec = importlib.util.spec_from_file_location("ai_qa_explorer_audit", TOOL)
assert spec and spec.loader
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def jsonbytes(x: dict) -> bytes:
    return (json.dumps(x, sort_keys=True) + "\n").encode()


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix="aiqa-explorer-audit-")
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.mission = {
            "schema_version": 1,
            "kind": "independent-ai-browser-mission",
            "classification": "SYNTHETIC_PUBLIC_DEVELOPMENT_ONLY",
            "max_actions": 4,
            "allowed_post_paths": ["/api/v1/multipass/preview-batch"],
            "expected_outcomes": [
                {"id": "accounting", "expected": "Two user records counted without data loss"}
            ],
        }
        self.mission_raw = jsonbytes(self.mission)
        self.plan_sha = sha(self.mission_raw)
        self.html_sha = "a" * 64
        self.png = audit.PNG_SIG + b"fake-image-for-deterministic-unit-test"
        self.png_sha = sha(self.png)
        for i in (1, 2):
            (self.root / f"observation-{i:03}.png").write_bytes(self.png)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("trace.txt", b"SYNTHETIC_NOT_REAL_CHROMIUM")
        self.trace = buf.getvalue()
        (self.root / "chromium-trace.zip").write_bytes(self.trace)
        self.versions = []
        for i in (1, 2):
            self.versions.append({
                "kind": "observation", "id": f"{i:064x}",
                "observation": {
                    "ordinal": i, "after": "initial-navigation" if i == 1 else "action-1",
                    "actions_used": i - 1,
                    "recent_http": [
                        {"method": "POST", "status": 200, "path": "/api/v1/multipass/preview-batch"}
                    ],
                    "screenshot": {
                        "name": f"observation-{i:03}.png",
                        "sha256": self.png_sha,
                    },
                },
            })
        self.act = {
            "kind": "ai_selected_action", "number": 1, "observation_id": self.versions[0]["id"],
            "model_reason": "Independent QA selected the observed input after reading the page.",
            "action": {"kind": "scroll", "direction": "down"},
            "action_error": None,
        }
        self.report = {
            "kind": "independent-ai-autonomous-exploration",
            "authority": "T0_ADVISORY_ONLY",
            "actual_model_invocation_verified": False,
            "human_equivalent_release_gate": False,
            "release_authorized": False,
            "product_full_user_e2e_verified": False,
            "mission_sha256": self.plan_sha,
            "runtime_html_sha256": self.html_sha,
            "browser_version": "145.0.0",
            "observations": 2,
            "actual_model_selected_actions": 1,
            "model_disposition": "NO_CONFIRMED_FINDING",
            "oracle_status": {"accounting": "OBSERVED"},
            "claimed_findings": [],
            "denied_network_requests": [],
            "browser_errors": [],
            "trace_sha256": sha(self.trace),
        }
        self.flush()

    def flush(self):
        self.report["trace_sha256"] = self.report.get("trace_sha256", sha(self.trace))
        (self.root / "final.json").write_bytes(jsonbytes(self.report))
        records = [self.versions[0], self.act, self.versions[1],
                   {"kind": "session_finished", "status": self.report["model_disposition"]}]
        (self.root / "events.jsonl").write_bytes(
            b"".join(jsonbytes(row) for row in records)
        )

    def assess(self):
        self.flush()
        return audit.assess(self.mission_raw, self.plan_sha, self.html_sha, self.root)

    def blocked(self, code):
        with self.assertRaisesRegex(audit.AuditBlocked, "^" + code + "$"):
            self.assess()

    def test_fixture_is_t0_consistency_only_not_real_browser_pass(self):
        result = self.assess()
        self.assertEqual(result["assessment"], "EVIDENCE_CONSISTENT_T0")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["independent_llm_provider_verified"])
        self.assertFalse(result["full_user_e2e_verified"])

    def test_forged_release_pass_denied(self):
        self.report["release_authorized"] = True
        self.blocked("FORGED_AUTHORITY")

    def test_forged_external_model_identity_denied(self):
        self.report["actual_model_invocation_verified"] = True
        self.blocked("FORGED_AUTHORITY")

    def test_mismatched_frozen_mission_denied(self):
        self.plan_sha = "f" * 64
        self.blocked("UNPINNED_MISSION")

    def test_runtime_html_mismatch_denied(self):
        self.report["runtime_html_sha256"] = "b" * 64
        self.blocked("STALE_OR_ALTERED_IDENTITY")

    def test_missing_live_action_denied(self):
        self.report["actual_model_selected_actions"] = 0
        self.blocked("ACTION_OR_OBSERVATION_COUNT_MISMATCH")

    def test_missing_browser_screenshot_denied(self):
        (self.root / "observation-002.png").unlink()
        self.blocked("MISSING_OR_SYMLINK_EVIDENCE")

    def test_tampered_screenshot_denied(self):
        (self.root / "observation-001.png").write_bytes(self.png + b"changed")
        self.blocked("SCREENSHOT_CHANGED")

    def test_symlink_screenshot_denied(self):
        p = self.root / "observation-001.png"
        p.unlink()
        p.symlink_to(self.root / "observation-002.png")
        self.blocked("MISSING_OR_SYMLINK_EVIDENCE")

    def test_changed_trace_denied(self):
        (self.root / "chromium-trace.zip").write_bytes(self.trace + b"changed")
        self.blocked("TRACE_CHANGED")

    def test_no_real_analysis_but_clear_claim_denied(self):
        for item in self.versions:
            item["observation"]["recent_http"] = []
        self.blocked("NO_REAL_ANALYSIS_OBSERVED")

    def test_skipped_action_or_fabricated_binding_denied(self):
        self.act["observation_id"] = "f" * 64
        self.blocked("UNVERIFIED_MODEL_CHOICE")

    def test_missing_ai_reason_denied(self):
        self.act["model_reason"] = "ok"
        self.blocked("UNVERIFIED_MODEL_CHOICE")

    def test_clear_result_cannot_ignore_errors(self):
        self.report["browser_errors"] = [{"message": "unhandled exception"}]
        self.blocked("FALSE_CLEAR_RESULT")

    def test_clear_result_cannot_ignore_blocked_writes(self):
        self.report["denied_network_requests"] = [{"reason": "CROSS_ORIGIN"}]
        self.blocked("FALSE_CLEAR_RESULT")

    def test_unverified_oracle_status_preserved(self):
        self.report["model_disposition"] = "UNVERIFIED"
        self.report["oracle_status"] = {"accounting": "UNVERIFIED"}
        result = self.assess()
        self.assertEqual(result["unverified_oracles"], ["accounting"])

    def test_forbidden_oracle_PASS_denied(self):
        self.report["oracle_status"] = {"accounting": "PASS"}
        self.blocked("INVALID_ORACLE_STATUS")

    def test_no_unsupported_claim_from_misordered_ledger(self):
        self.versions[1]["observation"]["ordinal"] = 1
        self.blocked("MISORDERED_OBSERVATION")


if __name__ == "__main__":
    unittest.main(verbosity=2)
