#!/usr/bin/env python3
"""Actual QA-engine Python source mutation in isolated disposable test copies.

Unlike semantic evidence mutation, these alter the function implementation and
execute the normal regression suite on each mutant. No product/production source
files are edited. This is deliberately restricted to the QA benchmark itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools" / "ai_qa_benchmark.py"
TEST = ROOT / "tools" / "test_ai_qa_benchmark.py"
REPLACEMENTS = [
    ("duplicate-id-check", 'require(k not in found, "DUPLICATE_ID")',
     'require(True, "DUPLICATE_ID")'),
    ("frozen-oracle-check", 'require(actual_plan_digest == expected_plan_digest, "UNPINNED_OR_CHANGED_ORACLE")',
     'require(True, "UNPINNED_OR_CHANGED_ORACLE")'),
    ("exact-head-check", 'require(plan["subject_head"] == expected_head, "STALE_OR_UNEXPECTED_HEAD")',
     'require(True, "STALE_OR_UNEXPECTED_HEAD")'),
    ("reject-artifact-symlink", 'require(not current.is_symlink(), "ARTIFACT_SYMLINK")',
     'require(True, "ARTIFACT_SYMLINK")'),
    ("artifact-integrity-hash", 'require(hashlib.sha256(raw).hexdigest() == digest, "ALTERED_ARTIFACT")',
     'require(True, "ALTERED_ARTIFACT")'),
    ("three-check-oracle", 'require(REQUIRED_CHECKS <= set(checks), "MISSING_INDEPENDENT_ORACLE")',
     'require(True, "MISSING_INDEPENDENT_ORACLE")'),
    ("missing-journeys-verification", '    if gaps:\n        assessment = "INCOMPLETE"',
     '    if False:\n        assessment = "INCOMPLETE"'),
    ("missed-seeded-faults", '    elif problems or missed:\n        assessment = "QUALITY_FAIL"',
     '    elif False:\n        assessment = "QUALITY_FAIL"'),
]


def run_test(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", "tools/test_ai_qa_benchmark.py"], cwd=root,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=25, check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Isolated source mutation probe for canonical QA benchmark")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.out.exists():
        raise SystemExit("REFUSE_OVERWRITE_EVIDENCE")
    source = SOURCE.read_text(encoding="utf-8")
    for name, before, after in REPLACEMENTS:
        if source.count(before) != 1 or before == after:
            raise SystemExit("INVALID_MUTANT_PATTERN: " + name)
    with tempfile.TemporaryDirectory(prefix="engineering-ai-qa-source-mutants-") as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        shutil.copy2(TEST, root / "tools" / TEST.name)
        sandbox_source = root / "tools" / SOURCE.name
        sandbox_source.write_text(source, encoding="utf-8")
        control = run_test(root)
        if control.returncode:
            raise SystemExit("BASELINE_REGRESSION_RED_NOT_VALID_MUTATION_SCORE")
        rows = []
        for name, before, after in REPLACEMENTS:
            sandbox_source.write_text(source.replace(before, after, 1), encoding="utf-8")
            try:
                result = run_test(root)
            except subprocess.TimeoutExpired:
                rows.append({"name": name, "status": "TIMEOUT_NOT_QUALIFIED"})
                continue
            # A test failure is a detected behavioral mutation. Syntax exceptions
            # are INVALID mutants, not valid kills; check compilation separately.
            compilation = subprocess.run([sys.executable, "-B", "-c",
                                          "import ast,sys;ast.parse(open(sys.argv[1]).read())",
                                          str(sandbox_source)], cwd=root,
                                         capture_output=True, text=True, timeout=5)
            if compilation.returncode:
                rows.append({"name": name, "status": "INVALID_MUTANT"})
            else:
                rows.append({"name": name, "status": "KILLED" if result.returncode else "SURVIVED",
                             "regression_exit_code": result.returncode,
                             "failing_test_count": result.stderr.count(" ... FAIL")
                             + result.stderr.count(" ... ERROR")})
    valid = [x for x in rows if x["status"] in ("KILLED", "SURVIVED")]
    killed = sum(x["status"] == "KILLED" for x in valid)
    record = {
        "kind": "bounded-python-source-mutation",
        "authority": "T0_ADVISORY_ONLY",
        "source_code_mutation": True,
        "source_scope": "engineering-system/tools/ai_qa_benchmark.py ONLY",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "baseline_regression": "PASS",
        "test_cases_per_mutant": 31,
        "total_candidates": len(REPLACEMENTS),
        "valid_mutants": len(valid),
        "killed": killed,
        "survived": len(valid) - killed,
        "score": round(killed / len(valid), 4) if valid else None,
        "details": rows,
        "external_product_acceptance": False,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in record.items() if k != "details"}, sort_keys=True))
    return 0 if len(valid) == len(REPLACEMENTS) and killed == len(valid) else 2


if __name__ == "__main__":
    sys.exit(main())
