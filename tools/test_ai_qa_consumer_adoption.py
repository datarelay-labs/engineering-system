#!/usr/bin/env python3
"""Adversarial tests: a YAML declaration or one green workflow is not adoption."""
from __future__ import annotations

import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

p = Path(__file__).with_name("ai_qa_consumer_adoption.py")
spec = importlib.util.spec_from_file_location("consumer_adoption", p)
assert spec and spec.loader
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)
HEAD = "a" * 40
OLD = "b" * 40
REPO = "datarelay-labs/engineering-system"


def config():
    body = {
        "engineering_system": {
            "version": "1.7.0", "mode": "adopted",
            "policy_epoch": 26, "governance_epoch": 1,
        },
        "project": {"name": "sensitive-customer-not-output"},
    }
    return {
        "type": "file",
        "content": base64.b64encode(json.dumps(body).encode()).decode(),
    }


def run(sha=HEAD, conclusion="success"):
    return {"head_sha": sha, "status": "completed",
            "conclusion": conclusion, "name": "Validate"}


class AdoptionTests(unittest.TestCase):
    def test_declared_not_verified_or_released(self):
        r = qa.summarize(REPO, HEAD, agents_present=True,
                         es=qa.policy(config()), runs=[run()],
                         origin="AUTHENTICATED_GITHUB_READ_ONLY")
        self.assertEqual(r["adoption"], "DECLARED_ADOPTED_NOT_RUNTIME_ATTESTED")
        self.assertEqual(r["ci_observation"],
                         "OBSERVED_WORKFLOWS_WITHOUT_GATE_COMPLETENESS")
        self.assertEqual(r["user_e2e"], "NOT_VERIFIED")
        self.assertEqual(r["release_authority"], "NO")
        self.assertFalse(r["adoption_contract_checked_locally"])
        self.assertNotIn("sensitive-customer", json.dumps(r))

    def test_canonical_host_not_misclassified(self):
        cfg = qa.policy(config())
        cfg["mode"] = "canonical"
        r = qa.summarize(REPO, HEAD, agents_present=True,
                         es=cfg, runs=[run()],
                         origin="AUTHENTICATED_GITHUB_READ_ONLY")
        self.assertEqual(r["adoption"], "CANONICAL_SOURCE_DECLARED")
        self.assertEqual(r["user_e2e"], "NOT_VERIFIED")

    def test_stale_ci_not_attested(self):
        r = qa.summarize(REPO, HEAD, agents_present=True,
                         es=qa.policy(config()), runs=[run(OLD)],
                         origin="AUTHENTICATED_GITHUB_READ_ONLY")
        self.assertEqual(r["ci_observation"], "NO_EXACT_HEAD_RUN_IN_QUERIED_WINDOW")
        self.assertEqual(r["exact_head_workflow_runs"], [])

    def test_exact_head_failure_and_absent_policy(self):
        r = qa.summarize(REPO, HEAD, agents_present=False,
                         es=None, runs=[run(), run(conclusion="failure")],
                         origin="AUTHENTICATED_GITHUB_READ_ONLY")
        self.assertEqual(r["adoption"], "POLICY_MISSING_ON_MAIN")
        self.assertEqual(r["ci_observation"], "FAILURES_OBSERVED_AT_EXACT_HEAD")
        self.assertEqual(r["user_e2e"], "NOT_VERIFIED")

    def test_malformed_source_or_ci_fail_closed(self):
        for bad_repo in ("../repo", "owner/..", "./repo"):
            with self.assertRaises(qa.AuditBlocked):
                qa.summarize(bad_repo, HEAD, agents_present=True,
                             es=qa.policy(config()), runs=[],
                             origin="AUTHENTICATED_GITHUB_READ_ONLY")
        for sha in ("a" * 39, "../secrets", ""):
            with self.assertRaises(qa.AuditBlocked):
                qa.summarize(REPO, sha, agents_present=True,
                             es=qa.policy(config()), runs=[],
                             origin="AUTHENTICATED_GITHUB_READ_ONLY")
        for row in ({"head_sha": HEAD, "name": "Validate",
                     "status": "completed", "conclusion": "impossibly_verified"},
                    {"head_sha": HEAD, "name": 123,
                     "status": "completed", "conclusion": "success"}):
            with self.assertRaises(qa.AuditBlocked):
                qa.summarize(REPO, HEAD, agents_present=True,
                             es=qa.policy(config()), runs=[row],
                             origin="AUTHENTICATED_GITHUB_READ_ONLY")

    def test_injected_actual_shape_github_read(self):
        def fetch(uri, **kwargs):
            if "/branches/main" in uri:
                return {"commit": {"sha": HEAD}}
            if "AGENTS.md" in uri:
                return {"type": "file", "content": "ignored"}
            if "project.yaml" in uri:
                return config()
            if "/actions/runs?" in uri:
                return {"workflow_runs": [run()]}
            raise AssertionError(uri)
        with patch.object(qa, "github", side_effect=fetch):
            r = qa.live(REPO)
        self.assertEqual(r["main_head"], HEAD)
        self.assertEqual(r["source_provenance"], "AUTHENTICATED_GITHUB_READ_ONLY")
        self.assertEqual(r["user_e2e"], "NOT_VERIFIED")

    def test_decode_policy_missing_and_broken(self):
        self.assertIsNone(qa.policy(None))
        with self.assertRaises(qa.AuditBlocked):
            qa.policy({"type": "symlink", "content": "a"})
        with self.assertRaises(qa.AuditBlocked):
            qa.policy({"type": "file", "content": base64.b64encode(b"[]").decode()})


if __name__ == "__main__":
    unittest.main()
