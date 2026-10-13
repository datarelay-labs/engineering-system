"""Security regression tests for authenticated user-gate runtime overrides."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import user_acceptance_contract as gate  # noqa: E402
override = gate
from test_user_acceptance_contract import evidence  # noqa: E402

CONTRACT_REL = "standards/USER_ACCEPTANCE.md"
PROFILE_REL = ".engineering/execution-profile.yaml"


def run(*args: str) -> None:
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True
    ).strip()


class OwnerRuntimeOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.repo = self.tmp / "candidate"
        run("git", "init", "-q", str(self.repo))
        for rel in (CONTRACT_REL, PROFILE_REL):
            target = self.repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / rel).read_bytes())
        run("git", "-C", str(self.repo), "remote", "add", "origin",
            "https://github.com/datarelay-labs/engineering-system.git")
        run("git", "-C", str(self.repo), "add", ".")
        run("git", "-C", str(self.repo), "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid", "commit", "-qm", "candidate")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.digest = hashlib.sha256(
            (self.repo / CONTRACT_REL).read_bytes()
        ).hexdigest()
        self.key = self.tmp / "key.pem"
        self.pub = self.tmp / "public.pem"
        run("/usr/bin/openssl", "genpkey", "-algorithm", "ED25519", "-out", str(self.key))
        run("/usr/bin/openssl", "pkey", "-in", str(self.key),
            "-pubout", "-out", str(self.pub))

    def case(self, gate_name: str = "SURFACE_RECONCILIATION") -> dict:
        data = evidence(gate_name)
        data.update(
            candidate_head=self.head, contract_sha256=self.digest, runtime="CODEX",
        )
        return data

    def make_receipt(self, data: dict) -> dict:
        now = int(time.time())
        return {
            "schema_version": 1,
            "kind": "owner-runtime-override-receipt",
            "purpose": "user-acceptance-runtime-override",
            "issuer": "engineering-system-trusted-owner-boundary",
            "target_repo": "datarelay-labs/engineering-system",
            "candidate_head": data["candidate_head"],
            "contract_path": data["contract_path"],
            "contract_sha256": data["contract_sha256"],
            "gate": data["gate"],
            "run_id": data["run_id"],
            "runtime": data["runtime"],
            "owner_actor": "RickLee-kr",
            "owner_authority": "admin",
            "owner_approval_ref": "github:issuecomment:123456",
            "receipt_id": "approval-cc12fafe",
            "issued_at_unix": now - 1,
            "expires_at_unix": now + 60,
        }

    def sign(self, receipt: dict, private: Path | None = None) -> dict:
        receipt = copy.deepcopy(receipt)
        receipt.pop("signature", None)
        msg = self.tmp / "message.json"
        sig = self.tmp / "signature.bin"
        msg.write_bytes(override._canonical_bytes(receipt))
        run("/usr/bin/openssl", "pkeyutl", "-sign",
            "-inkey", str(private or self.key), "-rawin", "-in", str(msg),
            "-out", str(sig))
        receipt["signature"] = base64.b64encode(sig.read_bytes()).decode("ascii")
        return receipt

    def check(self, data: dict, anchor: Path | None = ...) -> None:
        path = self.tmp / "user_evidence.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with mock.patch.object(override, "_trusted_anchor", return_value=(
            self.pub if anchor is ... else anchor
        )):
            gate.validate_gate(path, self.repo, data["gate"])

    def expect_block(self, data: dict, token: str, anchor: Path | None = ...) -> None:
        with self.assertRaises(gate.ContractError) as ctx:
            self.check(data, anchor)
        self.assertIn(token, str(ctx.exception))

    def test_default_primary_and_unapproved_alternate(self) -> None:
        primary = self.case()
        primary["runtime"] = "CHATGPT_CHAT"
        self.check(primary)
        self.expect_block(self.case(), "EXECUTION_PROFILE_RUNTIME_MISMATCH")
        primary["owner_override"] = True
        self.expect_block(primary, "EVIDENCE_SCHEMA_INVALID")

    def test_candidate_head_change_during_validation_blocks(self) -> None:
        """Both the normal and signed selection must remain bound to live HEAD."""
        for alternate in (False, True):
            with self.subTest(alternate=alternate):
                data = self.case()
                if alternate:
                    data["owner_runtime_override"] = self.sign(self.make_receipt(data))
                else:
                    data["runtime"] = "CHATGPT_CHAT"
                original = gate._current_head
                calls = [0]

                def moving_head(root: Path) -> str:
                    calls[0] += 1
                    return "f" * 40 if calls[0] > 1 else original(root)

                with mock.patch.object(gate, "_current_head", side_effect=moving_head):
                    self.expect_block(data, "CANDIDATE_HEAD_CHANGED_DURING_VALIDATION")

    def test_signed_alternate_runtime_with_direct_persona_both_gates(self) -> None:
        for name in ("SURFACE_RECONCILIATION", "FULL_USER_E2E"):
            with self.subTest(gate=name):
                data = self.case(name)
                data["owner_runtime_override"] = self.sign(self.make_receipt(data))
                self.check(data)
                data["direct_persona_execution"] = False
                self.expect_block(data, "PERSONA_EXECUTION_CLAIM_INVALID")

    def test_valid_signature_still_never_grants_product_quality_closure(self) -> None:
        surface = self.case("SURFACE_RECONCILIATION")
        e2e = self.case("FULL_USER_E2E")
        surface["owner_runtime_override"] = self.sign(self.make_receipt(surface))
        e2e["owner_runtime_override"] = self.sign(self.make_receipt(e2e))
        surface_path = self.tmp / "surface.json"
        e2e_path = self.tmp / "e2e.json"
        surface_path.write_text(json.dumps(surface), encoding="utf-8")
        e2e_path.write_text(json.dumps(e2e), encoding="utf-8")
        stdout = io.StringIO()
        with mock.patch.object(override, "_trusted_anchor", return_value=self.pub):
            with redirect_stdout(stdout):
                gate.quality_close(surface_path, e2e_path, self.repo)
        self.assertIn("PRODUCT_QUALITY_CLOSURE_STRUCTURAL=PASS", stdout.getvalue())
        self.assertIn("PRODUCT_QUALITY_CLOSURE=BLOCK", stdout.getvalue())
        self.assertIn("TRUSTED_PERSONA_ATTESTATION=REQUIRED", stdout.getvalue())
        self.assertIn("AUTHORIZES_RELEASE=NO", stdout.getvalue())

    def test_quality_close_rechecks_head_after_both_gates(self) -> None:
        surface = self.case("SURFACE_RECONCILIATION")
        e2e = self.case("FULL_USER_E2E")
        surface["runtime"] = e2e["runtime"] = "CHATGPT_CHAT"
        surface_path = self.tmp / "moving_surface.json"
        e2e_path = self.tmp / "moving_e2e.json"
        surface_path.write_text(json.dumps(surface), encoding="utf-8")
        e2e_path.write_text(json.dumps(e2e), encoding="utf-8")

        original = gate._current_head
        calls = [0]

        def moving_head(root: Path) -> str:
            calls[0] += 1
            # Initial closure HEAD + twice per gate = five reads; move before success.
            return "f" * 40 if calls[0] > 5 else original(root)

        with mock.patch.object(gate, "_current_head", side_effect=moving_head):
            with self.assertRaises(gate.ContractError) as ctx:
                gate.quality_close(surface_path, e2e_path, self.repo)
        self.assertIn("CANDIDATE_HEAD_CHANGED_DURING_VALIDATION", str(ctx.exception))

    def test_invalid_signature_wrong_key_and_missing_trust(self) -> None:
        data = self.case()
        data["owner_runtime_override"] = self.sign(self.make_receipt(data))
        self.expect_block(data, "OWNER_RUNTIME_OVERRIDE_TRUST_UNAVAILABLE", None)
        forged_key = self.tmp / "forged-key.pem"
        forged_pub = self.tmp / "forged-public.pem"
        run("/usr/bin/openssl", "genpkey", "-algorithm", "ED25519",
            "-out", str(forged_key))
        run("/usr/bin/openssl", "pkey", "-in", str(forged_key),
            "-pubout", "-out", str(forged_pub))
        data["owner_runtime_override"] = self.sign(self.make_receipt(data), forged_key)
        self.expect_block(data, "OWNER_RUNTIME_OVERRIDE_SIGNATURE_INVALID")
        data["owner_runtime_override"]["signature"] = "not-valid-base64"
        self.expect_block(data, "OWNER_RUNTIME_OVERRIDE_SIGNATURE_INVALID")

    def test_scope_bound_to_current_repository_head_contract_run_gate_runtime(self) -> None:
        baseline = self.case()
        signed = self.sign(self.make_receipt(baseline))
        for field, changed in (
            ("target_repo", "datarelay-labs/datarelay-link"),
            ("candidate_head", "a" * 40),
            ("contract_path", "docs/other.md"),
            ("contract_sha256", "b" * 64),
            ("gate", "FULL_USER_E2E"),
            ("run_id", "run-other"),
            ("runtime", "OTHER_RUNTIME"),
        ):
            with self.subTest(field=field):
                data = copy.deepcopy(baseline)
                data["owner_runtime_override"] = dict(signed, **{field: changed})
                self.expect_block(data, "OWNER_RUNTIME_OVERRIDE_SCOPE_MISMATCH")

        data = copy.deepcopy(baseline)
        data["owner_runtime_override"] = signed
        original_git = gate._git
        def untrusted_origin(root: Path, *args: str, **kwargs):
            if args == ("remote", "get-url", "origin"):
                return "https://evil.example/datarelay-labs/engineering-system.git"
            return original_git(root, *args, **kwargs)

        # An origin with an attacker host is never an authority.
        with mock.patch.object(gate, "_git", side_effect=untrusted_origin):
            self.expect_block(data, "OWNER_RUNTIME_OVERRIDE_REPOSITORY_UNVERIFIED")

    def test_expired_future_and_overlong_receipt(self) -> None:
        data = self.case()
        for variant in ("expired", "future", "long"):
            with self.subTest(variant=variant):
                receipt = self.make_receipt(data)
                now = int(time.time())
                if variant == "expired":
                    receipt.update(issued_at_unix=now-70, expires_at_unix=now-1)
                if variant == "future":
                    receipt.update(issued_at_unix=now+70, expires_at_unix=now+120)
                if variant == "long":
                    receipt.update(issued_at_unix=now-10, expires_at_unix=now+4000)
                data["owner_runtime_override"] = self.sign(receipt)
                self.expect_block(data, "OWNER_RUNTIME_OVERRIDE")

    def test_tampering_and_unauthorized_self_assertions(self) -> None:
        data = self.case()
        signed = self.sign(self.make_receipt(data))
        for field, changed in (
            ("owner_actor", "OtherUser"),
            ("owner_authority", "write"),
            ("owner_approval_ref", "self:approved"),
            ("receipt_id", "new-approval-ref-1"),
        ):
            with self.subTest(field=field):
                data["owner_runtime_override"] = dict(signed, **{field: changed})
                self.expect_block(data, "OWNER_RUNTIME_OVERRIDE_SIGNATURE_INVALID")
        data["owner_runtime_override"] = dict(signed, issuer="self")
        self.expect_block(data, "EVIDENCE_SCHEMA_INVALID")
        data["owner_runtime_override"] = dict(signed, key_path=str(self.pub))
        self.expect_block(data, "EVIDENCE_SCHEMA_INVALID")
        primary = self.case()
        primary["runtime"] = "CHATGPT_CHAT"
        primary["owner_runtime_override"] = signed
        self.expect_block(primary, "OWNER_RUNTIME_OVERRIDE_NOT_NEEDED")

    def test_unsigned_malformed_and_untrusted_anchor_are_fail_closed(self) -> None:
        data = self.case()
        data["owner_runtime_override"] = self.make_receipt(data)
        self.expect_block(data, "EVIDENCE_SCHEMA_INVALID")
        data["owner_runtime_override"] = self.sign(self.make_receipt(data))
        with mock.patch.object(override, "ANCHOR", self.pub):
            # The real resolver refuses a user-owned temporary public key.
            self.assertIsNone(override._trusted_anchor())
        self.expect_block(data, "OWNER_RUNTIME_OVERRIDE_TRUST_UNAVAILABLE", None)


if __name__ == "__main__":
    unittest.main()
