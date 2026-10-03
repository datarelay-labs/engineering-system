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
        "executor": "CHATGPT",
        "final_auditor": "CHATGPT",
        "chatgpt_direct_persona_execution": True,
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


class UserAcceptanceTests(unittest.TestCase):
    def run_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(TOOL), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def write(self, base: Path, name: str, data: dict) -> Path:
        path = base / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_each_gate_and_same_head_close_pass(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            surface = self.write(base, "surface.json", evidence("SURFACE_RECONCILIATION"))
            e2e = self.write(base, "e2e.json", evidence("FULL_USER_E2E"))
            cp = self.run_tool("validate-gate", "--evidence", str(surface))
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            cp = self.run_tool(
                "quality-close",
                "--surface-evidence", str(surface),
                "--e2e-evidence", str(e2e),
                "--expected-head", HEAD,
            )
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            self.assertIn("PRODUCT_QUALITY_CLOSURE=PASS", cp.stdout)
            self.assertIn("USER_ACCEPTANCE_EXECUTOR=CHATGPT", cp.stdout)
            self.assertIn("AUTHORIZES_RELEASE=NO", cp.stdout)

    def test_chatgpt_direct_persona_is_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            data = evidence("SURFACE_RECONCILIATION")
            data["chatgpt_direct_persona_execution"] = False
            path = self.write(Path(d), "surface.json", data)
            cp = self.run_tool("validate-gate", "--evidence", str(path))
            self.assertEqual(cp.returncode, 3)
            self.assertIn("CHATGPT_DIRECT_PERSONA_EXECUTION_REQUIRED", cp.stdout)

    def test_surface_requires_full_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            data = evidence("SURFACE_RECONCILIATION")
            data["public_surface_coverage_pct"] = 99
            path = self.write(Path(d), "surface.json", data)
            cp = self.run_tool("validate-gate", "--evidence", str(path))
            self.assertEqual(cp.returncode, 3)
            self.assertIn("PUBLIC_SURFACE_COVERAGE_INCOMPLETE", cp.stdout)

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

    def test_quality_close_requires_same_exact_head(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            surface = self.write(base, "surface.json", evidence("SURFACE_RECONCILIATION"))
            other = evidence("FULL_USER_E2E")
            other["candidate_head"] = "c" * 40
            e2e = self.write(base, "e2e.json", other)
            cp = self.run_tool(
                "quality-close",
                "--surface-evidence", str(surface),
                "--e2e-evidence", str(e2e),
                "--expected-head", HEAD,
            )
            self.assertEqual(cp.returncode, 3)
            self.assertIn("CANDIDATE_HEAD_MISMATCH", cp.stdout)


if __name__ == "__main__":
    unittest.main()
