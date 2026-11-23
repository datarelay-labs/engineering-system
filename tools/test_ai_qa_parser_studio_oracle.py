#!/usr/bin/env python3
"""Adversarial local fixtures for independent Parser Studio outcome oracle.

The fixture images and traces are synthetic placeholders; no browser PASS is claimed.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "adapters" / "ai-qa" / "parser-studio-oracle.py"
spec = importlib.util.spec_from_file_location("ai_qa_parser_studio_oracle", MODULE_PATH)
assert spec and spec.loader
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class OracleTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix="ai-qa-oracle-fixture-")
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.records = [
            {"event_type": "alert", "action": "blocked", "src_ip": "192.0.2.7", "dest_port": 443},
            {"event_type": "alert", "action": "allowed", "src_ip": "192.0.2.8", "dest_port": 53},
        ]
        self.mission = {
            "classification": "SYNTHETIC_SECURITY_EVENTS_NO_CUSTOMER_DATA",
            "input_records": self.records,
            "independent_oracle": {"original_event_count": 2},
        }
        self.raw_mission = json.dumps(self.mission, sort_keys=True).encode()
        self.digest = sha(self.raw_mission)
        data = "\n".join(json.dumps(x, separators=(",", ":"), ensure_ascii=False) for x in self.records).encode()
        events = []
        start = 0
        for i, row in enumerate(self.records):
            piece = json.dumps(row, separators=(",", ":")).encode()
            spans = []
            for key, val in row.items():
                encoded = json.dumps(val, separators=(",", ":")).encode()
                # The serialized value is uniquely located after the field's key.
                k = piece.index(("\"" + key + "\":").encode()) + len(key) + 3
                spans.append({"path": key, "startByte": k, "endByte": k + len(encoded)})
            events.append({
                "index": i, "startByte": start, "endByte": start + len(piece),
                "preview": {"status": "SOURCE_PARSED", "syntax": "JSON", "sourceSha256": sha(piece),
                            "sourceBytes": len(piece), "fields": row, "spans": spans,
                            "residuals": [], "issues": []},
            })
            start += len(piece) + (1 if i < len(self.records) - 1 else 0)
        backend = {
            "contract": "parser-studio.multipass-source-batch-preview.v0.1",
            "sourceSha256": sha(data), "sourceBytes": len(data), "eventCount": len(self.records),
            "nativeVerified": False, "targetVerified": False,
            "events": events,
        }
        raw_response = json.dumps(backend, separators=(",", ":"))
        self.capture = {
            "mission_sha256": self.digest, "input_sha256": sha(data), "html_status": 200,
            "errors": [], "thirdparty": [],
            "summary": "2 of 2 log events parsed completely",
            "responses": [{
                "url": "https://app.parser.xdr.ooo/api/v1/multipass/preview-batch",
                "status": 200, "body": raw_response, "sha256": sha(raw_response.encode())
            }],
        }
        self.draft = {
            "mission_sha256": self.digest, "source_sha256": sha(data),
            "artifacts": [], "errors": [], "network": [
                {"path": "/api/v1/parser-pairs/generate", "status": 200},
            ],
            "generated_panel": "Parser files created successfully\nThe files have not been installed in a live Stellar system.",
        }
        for i, (name, raw) in enumerate([
            ("customized_demo_firewall.conf", b"customized_demo_firewall port=6801\n" * 8),
            ("customized_demo_firewall", b"Base64.strict_decode64 customized_demo_firewall\n" * 8),
        ]):
            f = self.root / ("generated-" + str(i) + ".bin")
            f.write_bytes(raw)
            self.draft["artifacts"].append({
                "filename": name, "bytes": len(raw), "sha256": sha(raw), "path": f.name,
            })
        for name in ("after-analysis.png", "review-fields.png", "draft-complete.png", "chromium-trace.zip"):
            (self.root / name).write_bytes(b"SYNTHETIC_ONLY_NOT_REAL_SCREENSHOT" * 60)

    def verify(self):
        return qa.assess(self.mission, self.capture, self.draft,
                         frozen_sha256=self.digest, raw_mission=self.raw_mission, root=self.root)

    def blocked(self, code):
        with self.assertRaisesRegex(qa.EvidenceError, "^" + code + "$"):
            self.verify()

    def changed_backend(self, fn):
        item = self.capture["responses"][0]
        body = json.loads(item["body"])
        fn(body)
        item["body"] = json.dumps(body, separators=(",", ":"))
        item["sha256"] = sha(item["body"].encode())

    def test_valid_advisory_fixture(self):
        result = self.verify()
        self.assertEqual(result["result"], "INDEPENDENT_ORACLE_PASS")
        self.assertEqual(result["verified_extracted_values_and_spans"], 8)
        self.assertEqual(result["authority"], "T0_ADVISORY_ONLY")
        self.assertFalse(result["human_equivalent_release_gate"])

    def test_missing_second_record_not_false_pass(self):
        self.changed_backend(lambda x: x["events"].pop())
        self.blocked("EVENTS_LENGTH_MISMATCH")

    def test_wrong_event_count(self):
        self.changed_backend(lambda x: x.update(eventCount=1))
        self.blocked("SILENT_RECORD_LOSS")

    def test_forged_parsed_status(self):
        self.changed_backend(lambda x: x["events"][0]["preview"].update(status="SOURCE_PARTIAL"))
        self.blocked("INCORRECT_PARSING_STATUS")

    def test_wrong_field_value(self):
        self.changed_backend(lambda x: x["events"][0]["preview"]["fields"].update(action="allowed"))
        self.blocked("SOURCE_FIELD_VALUE_MISMATCH")

    def test_wrong_field_byte_span(self):
        self.changed_backend(lambda x: x["events"][0]["preview"]["spans"][0].update(startByte=0))
        self.blocked("INCORRECT_SOURCE_BYTE_SPAN")

    def test_modified_backend_response_not_accepted(self):
        self.capture["responses"][0]["body"] += "x"
        self.blocked("CAPTURED_RESPONSE_TAMPER")

    def test_mission_changed(self):
        self.mission["input_records"][0]["action"] = "allowed"
        self.blocked("OBSERVED_SOURCE_MISMATCH")

    def test_expected_plan_digest_altered(self):
        self.digest = "f" * 64
        self.blocked("ORACLE_TAMPER")

    def test_thirdparty_model_request(self):
        self.capture["thirdparty"].append({"host": "example.com", "method": "POST"})
        self.blocked("UNEXPECTED_THIRD_PARTY_EGRESS")

    def test_missing_browser_trace(self):
        (self.root / "chromium-trace.zip").unlink()
        self.blocked("MISSING_EVIDENCE")

    def test_missing_download(self):
        self.draft["artifacts"].pop()
        self.blocked("MISSING_PARSER_PAIR_DOWNLOAD")

    def test_tampered_download(self):
        original = (self.root / "generated-1.bin").read_bytes()
        (self.root / "generated-1.bin").write_bytes(b"X" + original[1:])
        self.blocked("DOWNLOAD_HASH_MISMATCH")

    def test_symlink_download(self):
        (self.root / "generated-1.bin").unlink()
        (self.root / "generated-1.bin").symlink_to(self.root / "generated-0.bin")
        self.blocked("SYMLINK_EVIDENCE")

    def test_source_hash_unmatched(self):
        self.draft["source_sha256"] = "0" * 64
        self.blocked("OBSERVED_SOURCE_MISMATCH")

    def test_public_site_failure(self):
        self.capture["html_status"] = 500
        self.blocked("BROWSER_ENTRY_NOT_SUCCESS")

    def test_non_json_preview(self):
        self.capture["responses"][0]["body"] = "not JSON"
        self.capture["responses"][0]["sha256"] = sha(b"not JSON")
        self.blocked("MALFORMED_PREVIEW")

    def test_altered_download_name(self):
        self.draft["artifacts"][0]["filename"] = "customized_risky_target"
        self.blocked("WRONG_DOWNLOAD_FILENAME")

    def test_false_native_install_claim(self):
        self.draft["generated_panel"] = "Parser files created successfully"
        self.blocked("MISSING_DRAFT_ONLY_DISCLOSURE")

    def test_original_trace_read_only(self):
        self.assertEqual(len(self.draft["artifacts"]), 2)
        self.assertEqual(self.verify()["original_records"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
