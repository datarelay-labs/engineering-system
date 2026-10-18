import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "user_acceptance_contract.py"
CONTRACT_REL = "standards/USER_ACCEPTANCE.md"
HEAD = subprocess.check_output(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
).strip()
DIGEST = hashlib.sha256(
    subprocess.check_output(["git", "-C", str(ROOT), "show", f"{HEAD}:{CONTRACT_REL}"])
).hexdigest()


def evidence(gate: str) -> dict:
    return {
        "schema_version": 1,
        "gate": gate,
        "run_id": "run-1",
        "candidate_head": HEAD,
        "contract_path": CONTRACT_REL,
        "contract_sha256": DIGEST,
        "contract_dirty": False,
        "final_status": "PASS",
        "head_unchanged": True,
        "executor": "EXECUTION_PROFILE",
        "runtime": "CHATGPT_CHAT",
        "final_auditor": "EXECUTION_PROFILE",
        "direct_persona_execution": True,
        "actual_user_surface": True,
        "scripted_user_substitution": False,
        "finding_accumulation_complete": True,
        "evidence_ledger_schema": "PASS",
        "summary_derived_from_ledger": True,
        "report_consistency": "PASS",
        "mandatory_total": 3,
        "mandatory_pass": 3,
        "mandatory_fail": 0,
        "mandatory_partial": 0,
        "mandatory_blocked": 0,
        "unresolved_blocking_findings": 0,
        "capability_coverage_pct": 100 if gate == "SURFACE_RECONCILIATION" else 0,
        "public_surface_coverage_pct": 100 if gate == "SURFACE_RECONCILIATION" else 0,
        "use_case_coverage_pct": 100 if gate == "FULL_USER_E2E" else 0,
        "real_effect_coverage_pct": 100 if gate == "FULL_USER_E2E" else 0,
        "cleanup_status": "PASS" if gate == "FULL_USER_E2E" else "NOT_APPLICABLE",
    }


def human_owner_evidence(gate: str) -> dict:
    """Test-shaped evidence; actor/provenance is intentionally NOT authenticated."""
    data = evidence(gate)
    data["user_execution_mode"] = "HUMAN_OWNER_WITH_CHATGPT_AUDIT"
    data["executor"] = "HUMAN_OWNER"
    data["human_owner_evidence"] = {
        "candidate_head": HEAD,
        "contract_sha256": DIGEST,
        "run_id": "run-1",
        "owner_instruction_reference":
            "https://github.com/datarelay-labs/engineering-system/issues/341#issuecomment-123",
        "owner_observation_reference":
            "https://github.com/datarelay-labs/engineering-system/issues/341#issuecomment-124",
        "chatgpt_audit_reference":
            "https://github.com/datarelay-labs/engineering-system/issues/341#issuecomment-125",
        "chatgpt_audit_claim": "PASS",
        "client": {
            "kind": "CHROME",
            "name": "Chrome",
            "version": "135.0.1.2",
            "target": "https://example.test",
            "process_observation_reference":
                "https://github.com/datarelay-labs/engineering-system/issues/341#issuecomment-126"
        },
        "started_at": "2026-10-12T00:00:00Z",
        "completed_at": "2026-10-12T00:10:00Z",
        "operator_reported_cleanup": (
            "PASS" if gate == "FULL_USER_E2E" else "NOT_APPLICABLE"
        ),
        "scenarios": [
            {
                "id": f"MISSION-{n}",
                "executed_at": f"2026-10-12T00:0{n}:00Z",
                "result": "PASS",
                "expected": f"Scenario {n} succeeds through real UI",
                "observed": f"Scenario {n} user-observed outcome",
                "evidence_reference":
                    f"https://github.com/datarelay-labs/engineering-system/issues/341#issuecomment-{200+n}",
                "screenshot_sha256": "a" * 64 if n == 1 else None,
                "readback_reference": (
                    None if n == 1 else
                    f"https://github.com/datarelay-labs/engineering-system/issues/341#issuecomment-{300+n}"
                ),
            } for n in (1, 2, 3)
        ],
    }
    return data


class UserAcceptanceTests(unittest.TestCase):
    def run_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(TOOL), *args, "--root", str(ROOT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def write(self, base: Path, name: str, data: dict) -> Path:
        path = base / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_explicit_human_owner_lane_remains_structural_only(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            surface = self.write(root, "human-surface.json",
                                 human_owner_evidence("SURFACE_RECONCILIATION"))
            e2e = self.write(root, "human-e2e.json",
                             human_owner_evidence("FULL_USER_E2E"))
            for item in (surface, e2e):
                cp = self.run_tool("validate-gate", "--evidence", str(item))
                self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
                self.assertIn("USER_ACCEPTANCE_GATE_STRUCTURAL=PASS", cp.stdout)
                self.assertIn("HUMAN_OWNER_ATTESTATION=UNVERIFIED", cp.stdout)
                self.assertIn("CHATGPT_AUDIT_PROVENANCE=UNVERIFIED", cp.stdout)
                self.assertIn("USER_GATE_EXECUTION_PASS=NOT_ESTABLISHED", cp.stdout)
            close = self.run_tool("quality-close", "--surface-evidence", str(surface),
                                  "--e2e-evidence", str(e2e))
            self.assertEqual(close.returncode, 0, close.stdout + close.stderr)
            self.assertIn("PRODUCT_QUALITY_CLOSURE=BLOCK", close.stdout)
            self.assertIn("CANDIDATE_FREEZE_ELIGIBLE=NO", close.stdout)

            # Atlas can keep a genuine AI-driven Surface Reconciliation and
            # opt into human-owner actions for Full User E2E only.
            self.write(root, "human-surface.json", evidence("SURFACE_RECONCILIATION"))
            mixed = self.run_tool("quality-close", "--surface-evidence", str(surface),
                                  "--e2e-evidence", str(e2e))
            self.assertEqual(mixed.returncode, 0, mixed.stdout + mixed.stderr)
            self.assertIn("SURFACE_EXECUTOR_CLAIM=EXECUTION_PROFILE", mixed.stdout)
            self.assertIn("E2E_EXECUTOR_CLAIM=HUMAN_OWNER", mixed.stdout)
            self.assertIn("PRODUCT_QUALITY_CLOSURE=BLOCK", mixed.stdout)

    def test_human_owner_git_origin_is_independent_of_evidence(self) -> None:
        from unittest.mock import patch
        from user_acceptance_contract import _committed_repository_origin, ContractError
        # Known local Git remotes are accepted in HTTPS and SSH forms; neither
        # a caller-selected evidence URL nor an untrusted non-GitHub remote is.
        with patch("user_acceptance_contract._git",
                   return_value="git@github.com:datarelay-labs/engineering-system.git"):
            self.assertEqual(
                _committed_repository_origin(ROOT), "datarelay-labs/engineering-system"
            )
        with patch("user_acceptance_contract._git",
                   return_value="https://attacker.test/datarelay-labs/engineering-system.git"):
            with self.assertRaisesRegex(ContractError, "ORIGIN_UNAVAILABLE"):
                _committed_repository_origin(ROOT)

    def test_human_owner_untrusted_claims_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            valid = human_owner_evidence("FULL_USER_E2E")
            def blocked(data: dict, expect: str = "USER_ACCEPTANCE=BLOCK") -> None:
                path = self.write(root, "invalid-human.json", data)
                cp = self.run_tool("validate-gate", "--evidence", str(path))
                self.assertEqual(cp.returncode, 3, cp.stdout + cp.stderr)
                self.assertIn(expect, cp.stdout)

            # Neither new legacy-mode defaults nor spoofed fields can authorize
            # human execution or an alternate coding runtime.
            stale = dict(valid)
            del stale["user_execution_mode"]
            blocked(stale)
            spoof = json.loads(json.dumps(valid))
            spoof["executor"] = "EXECUTION_PROFILE"
            blocked(spoof, "HUMAN_OWNER_EXECUTOR_REQUIRED")
            spoof = json.loads(json.dumps(valid))
            spoof["runtime"] = "CODEX"
            # The human-owner lane must retain ChatGPT final audit, not
            # merely reject an unsanctioned profile mismatch.
            blocked(spoof, "OWNER_RUNTIME_OVERRIDE_HUMAN_OWNER_MODE_FORBIDDEN")
            spoof = json.loads(json.dumps(valid))
            spoof["owner_authenticated"] = True
            blocked(spoof)
            spoof = json.loads(json.dumps(valid))
            spoof["human_owner_evidence"]["human_identity_verified"] = True
            blocked(spoof)
            spoof = json.loads(json.dumps(valid))
            spoof.pop("human_owner_evidence")
            blocked(spoof, "HUMAN_OWNER_EVIDENCE_MISSING")

            # Bind the human observation to the exact candidate, committed
            # contract and run identity; no URL/boolean alone is trustworthy.
            for field, value in (
                ("candidate_head", "b" * 40),
                ("contract_sha256", "b" * 64),
                ("run_id", "another-run"),
            ):
                spoof = json.loads(json.dumps(valid))
                spoof["human_owner_evidence"][field] = value
                blocked(spoof, "HUMAN_OWNER_" + field.upper() + "_MISMATCH")
            for field in ("owner_instruction_reference", "owner_observation_reference",
                          "chatgpt_audit_reference"):
                spoof = json.loads(json.dumps(valid))
                spoof["human_owner_evidence"][field] = ""
                blocked(spoof)

            for mutate in (
                lambda d: d["human_owner_evidence"]["scenarios"].pop(),
                lambda d: d["human_owner_evidence"]["scenarios"][1].update({"id": "MISSION-1"}),
                lambda d: d["human_owner_evidence"]["scenarios"][0].update({"result": "BLOCKED"}),
                lambda d: d["human_owner_evidence"]["scenarios"][0].update({
                    "screenshot_sha256": None, "readback_reference": None
                }),
                lambda d: d["human_owner_evidence"]["scenarios"][0].update({
                    "executed_at": "2026-10-13T00:02:00Z"
                }),
                lambda d: d["human_owner_evidence"]["scenarios"][0].update({
                    "evidence_reference": "https://fake.example/x"
                }),
                lambda d: d["human_owner_evidence"]["client"].update({
                    "target": "http://external.example/?api_key=secret"
                }),
                lambda d: d["human_owner_evidence"]["client"].update({
                    "target": "http://[invalid"
                }),
                lambda d: d["human_owner_evidence"]["client"].update({
                    "process_observation_reference": "https://github.com/other/repo/issues/1#issuecomment-2"
                }),
                lambda d: d["human_owner_evidence"].update({
                    "chatgpt_audit_claim": "TRUSTED_OWNER"
                }),
                lambda d: d["human_owner_evidence"].update({
                    "owner_instruction_reference": "https://example.test/?token=secret"
                }),
                lambda d: d.update({"scripted_user_substitution": True}),
                lambda d: d.update({"mandatory_pass": 2}),
                lambda d: d.update({"final_auditor": "HUMAN_OWNER"}),
            ):
                spoof = json.loads(json.dumps(valid))
                mutate(spoof)
                blocked(spoof)

            # An attacker-controlled repo can offer internally consistent
            # GitHub-looking references. It MUST NOT qualify as evidence for
            # the candidate's independently resolved Git origin repository.
            spoof = json.loads(json.dumps(valid))
            foreign = "https://github.com/attacker/false-e2e/issues/1#issuecomment-"
            human = spoof["human_owner_evidence"]
            for idx, field in enumerate((
                "owner_instruction_reference", "owner_observation_reference",
                "chatgpt_audit_reference"
            )):
                human[field] = foreign + str(idx + 11)
            human["client"]["process_observation_reference"] = foreign + "14"
            for idx, scenario in enumerate(human["scenarios"]):
                scenario["evidence_reference"] = foreign + str(101 + idx)
                if scenario["readback_reference"]:
                    scenario["readback_reference"] = foreign + str(201 + idx)
            blocked(spoof, "HUMAN_OWNER_REFERENCE_REPOSITORY_MISMATCH")

            # Legacy AI mode rejects a forged nested human receipt and an
            # explicitly claimed human-mode executor.
            ai = evidence("FULL_USER_E2E")
            ai["human_owner_evidence"] = valid["human_owner_evidence"]
            blocked(ai, "HUMAN_OWNER_EVIDENCE_UNEXPECTED")

    def test_machine_check_is_structural_only(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            surface = self.write(base, "surface.json", evidence("SURFACE_RECONCILIATION"))
            e2e = self.write(base, "e2e.json", evidence("FULL_USER_E2E"))
            cp = self.run_tool("validate-gate", "--evidence", str(surface))
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            self.assertIn("USER_ACCEPTANCE_GATE_STRUCTURAL=PASS", cp.stdout)
            self.assertIn("USER_GATE_EXECUTION_PASS=NOT_ESTABLISHED", cp.stdout)
            cp = self.run_tool(
                "quality-close",
                "--surface-evidence", str(surface),
                "--e2e-evidence", str(e2e),
            )
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            self.assertIn("PRODUCT_QUALITY_CLOSURE_STRUCTURAL=PASS", cp.stdout)
            self.assertIn("EXECUTOR_PROVENANCE=UNVERIFIED", cp.stdout)
            self.assertIn("CANDIDATE_FREEZE_ELIGIBLE=NO", cp.stdout)

    def test_chatgpt_direct_persona_is_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            data = evidence("SURFACE_RECONCILIATION")
            data["direct_persona_execution"] = False
            path = self.write(Path(d), "surface.json", data)
            cp = self.run_tool("validate-gate", "--evidence", str(path))
            self.assertEqual(cp.returncode, 3)
            self.assertIn("PERSONA_EXECUTION_CLAIM_INVALID", cp.stdout)

    def test_surface_requires_full_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            data = evidence("SURFACE_RECONCILIATION")
            data["public_surface_coverage_pct"] = 99
            path = self.write(Path(d), "surface.json", data)
            cp = self.run_tool("validate-gate", "--evidence", str(path))
            self.assertEqual(cp.returncode, 3)
            self.assertIn("PUBLIC_SURFACE_COVERAGE_INCOMPLETE", cp.stdout)

    def test_stale_candidate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            data = evidence("SURFACE_RECONCILIATION")
            data["candidate_head"] = "c" * 40
            path = self.write(Path(d), "surface.json", data)
            cp = self.run_tool("validate-gate", "--evidence", str(path))
            self.assertEqual(cp.returncode, 3)
            self.assertIn("CANDIDATE_HEAD_NOT_CURRENT", cp.stdout)


    def test_runtime_comes_from_committed_candidate_profile(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for rel in (CONTRACT_REL, ".engineering/execution-profile.yaml"):
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(subprocess.check_output(
                    ["git", "-C", str(ROOT), "show", f"{HEAD}:{rel}"]))
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test",
                            "-c", "user.email=test@example.invalid",
                            "commit", "-qm", "candidate"], check=True)
            candidate = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            profile = root / ".engineering/execution-profile.yaml"
            profile.write_text(profile.read_text().replace("primary: CHATGPT_CHAT", "primary: CODEX"))
            data = evidence("SURFACE_RECONCILIATION")
            data["candidate_head"] = candidate
            path = self.write(root, "evidence.json", data)
            def run():
                return subprocess.run(["python3", str(TOOL), "validate-gate",
                                       "--root", str(root), "--evidence", str(path)],
                                      capture_output=True, text=True)
            cp = run()
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            data["runtime"] = "CODEX"
            self.write(root, "evidence.json", data)
            cp = run()
            self.assertEqual(cp.returncode, 3, cp.stdout + cp.stderr)
            self.assertIn("EXECUTION_PROFILE_RUNTIME_MISMATCH", cp.stdout)

    def test_e2e_requires_real_effect_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            data = evidence("FULL_USER_E2E")
            data["real_effect_coverage_pct"] = 80
            data["cleanup_status"] = "FAIL"
            path = self.write(Path(d), "e2e.json", data)
            cp = self.run_tool("validate-gate", "--evidence", str(path))
            self.assertEqual(cp.returncode, 3)
            self.assertIn("REAL_EFFECT_COVERAGE_INCOMPLETE", cp.stdout)
            self.assertIn("CLEANUP_NOT_PASS", cp.stdout)
    def test_scripted_substitution_and_blocked_mandatory_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            data = evidence("SURFACE_RECONCILIATION")
            data["scripted_user_substitution"] = True
            data["mandatory_pass"] = 2
            data["mandatory_blocked"] = 1
            path = self.write(Path(d), "surface.json", data)
            cp = self.run_tool("validate-gate", "--evidence", str(path))
            self.assertEqual(cp.returncode, 3)
            self.assertIn("SCRIPTED_USER_SUBSTITUTION", cp.stdout)
            self.assertIn("MANDATORY_BLOCKED_NONZERO", cp.stdout)

if __name__ == "__main__":
    unittest.main()
