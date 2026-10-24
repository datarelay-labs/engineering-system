#!/usr/bin/env python3
"""Synthetic-only example. This does NOT execute a real browser or user E2E gate."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEAD = "a" * 40


def write_json(path: Path, obj: dict) -> str:
    raw = (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="synthetic-ai-qa-") as temporary:
        root = Path(temporary)
        proof = root / "proof"
        proof.mkdir()

        def artifact(label: str) -> dict:
            data = ("SYNTHETIC, NOT ACTUAL QA: " + label + "\n").encode()
            (proof / label).write_bytes(data)
            return {"path": label, "sha256": hashlib.sha256(data).hexdigest()}

        plan = {
            "schema_version": 1, "kind": "ai-qa-holdout",
            "target_repo": "datarelay-labs/engineering-system", "subject_head": HEAD,
            "intent_revision": 1, "plan_id": "synthetic-example-only",
            "journeys": [
                {"id": "broken", "required_checks": ["public_action", "final_outcome", "independent_oracle"]},
                {"id": "clean", "required_checks": ["public_action", "final_outcome", "independent_oracle"]},
            ],
            "faults": [{"id": "demo-seeded-bug", "journey_id": "broken", "severity": "CRITICAL"}],
        }

        def checks(journey: str, values: list[str]) -> list[dict]:
            return [
                {"id": name, "status": state, "artifact": artifact(journey + "-" + name)}
                for name, state in zip(
                    ["public_action", "final_outcome", "independent_oracle"], values, strict=True
                )
            ]

        observation = {
            "schema_version": 1, "kind": "ai-qa-observation",
            "target_repo": plan["target_repo"], "subject_head": HEAD,
            "intent_revision": 1, "plan_id": plan["plan_id"],
            "developer_actor": {"identity": "demo-dev", "context_id": "demo-dev-context"},
            "qa_actor": {"identity": "demo-qa", "context_id": "demo-qa-context"},
            "journeys": [
                {"id": "broken", "status": "FAIL", "checks": checks("broken", ["PASS", "FAIL", "FAIL"])},
                {"id": "clean", "status": "PASS", "checks": checks("clean", ["PASS", "PASS", "PASS"])},
            ],
            "findings": [
                {"id": "demo-finding", "fault_id": "demo-seeded-bug", "journey_id": "broken",
                 "artifact": artifact("demo-finding")}
            ],
        }
        manifest_digest = write_json(root / "holdout.json", plan)
        write_json(root / "observations.json", observation)
        cmd = [
            sys.executable, "-B", str(ROOT / "tools" / "ai_qa_benchmark.py"),
            "--plan", str(root / "holdout.json"),
            "--report", str(root / "observations.json"),
            "--artifacts-root", str(proof),
            "--expected-head", HEAD,
            "--expected-plan-sha256", manifest_digest,
        ]
        print("EXAMPLE_TYPE=SYNTHETIC_ONLY_NO_REAL_BROWSER_NO_HUMAN_EQUIVALENT_E2E")
        outcome = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=10)
        print(outcome.stdout.strip())
        if outcome.stderr:
            print(outcome.stderr.strip(), file=sys.stderr)
        return outcome.returncode


if __name__ == "__main__":
    sys.exit(main())
