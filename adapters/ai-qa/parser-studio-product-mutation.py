#!/usr/bin/env python3
"""Mutate real Parser Studio TS source only inside a disposable Git archive.

This is a focused diagnostic of original product tests vs. one independent,
externally authored requirements holdout. It never edits the product repository,
runs its browser, deploys, or grants release/user E2E authority.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HEAD_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
SOURCE = Path("src/guided/normalizationValidation.ts")
PRODUCT_TEST = Path("src/guided/normalizationValidation.test.ts")
HOLDOUT_TEST = Path("src/guided/aiQaNormalizationHoldout.test.ts")
HOLDOUT_SOURCE = Path(__file__).with_name("parser-studio-normalization-holdout.test.ts")
MUTANTS = (
    (
        "nested-target-collision-not-detected",
        "return a===b||a.startsWith(b+'.')||b.startsWith(a+'.');",
        "return a===b;",
    ),
    (
        "collision-check-disabled",
        "other.id!==mapping.id&&overlaps(other.target,mapping.target)",
        "false&&overlaps(other.target,mapping.target)",
    ),
    (
        "missing-source-check-disabled",
        "if(!found.found)",
        "if(false)",
    ),
    (
        "reserved-field-classified-wrong",
        "findings.push({code:'TARGET_RESERVED',source:mapping.source,target:mapping.target,event:null});",
        "findings.push({code:'BACKEND_TYPE_UNSUPPORTED',source:mapping.source,target:mapping.target,event:null});",
    ),
    (
        "reporting-truncated-to-one-finding",
        "return findings.slice(0,300);",
        "return findings.slice(0,1);",
    ),
)


class Unsafe(Exception):
    pass


def must(ok: bool, reason: str) -> None:
    if not ok:
        raise Unsafe(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def obtain_snapshot(source_root: Path, expected_head: str) -> bytes:
    must(source_root.is_dir() and (source_root / ".git").exists(), "INVALID_PRODUCT_CHECKOUT")
    actual = subprocess.run(["git", "-C", str(source_root), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    must(actual == expected_head, "SOURCE_HEAD_MISMATCH")
    raw = subprocess.run(["git", "-C", str(source_root), "archive", "--format=tar",
                          expected_head], check=True, capture_output=True, timeout=30).stdout
    must(0 < len(raw) <= 50_000_000, "OVERSIZED_ARCHIVE")
    return raw


def typescript_parses(root: Path, source: Path) -> bool:
    """Reject syntax-error mutants before counting a failing test as a kill."""
    script = (
        "const ts=require('typescript'),fs=require('fs');"
        "const f=process.argv[1],s=fs.readFileSync(f,'utf8');"
        "const sf=ts.createSourceFile(f,s,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);"
        "process.exit(sf.parseDiagnostics.length?3:0);"
    )
    result = subprocess.run(["node", "-e", script, str(source)], cwd=root,
                            capture_output=True, text=True, timeout=12, check=False)
    return result.returncode == 0


def execute_test(root: Path, *, holdout: bool) -> tuple[str, str]:
    exe = root / "node_modules" / ".bin" / "vitest"
    selected = [str(PRODUCT_TEST)]
    if holdout:
        selected.append(str(HOLDOUT_TEST))
    try:
        ran = subprocess.run([str(exe), "run", *selected, "--maxWorkers=2"],
                             cwd=root, env={**os.environ, "CI": "1", "NO_COLOR": "1"},
                             capture_output=True, text=True, timeout=55, check=False)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "TIMEOUT"
    last = (ran.stdout + "\n" + ran.stderr)[-1800:]
    if ran.returncode == 0:
        return "PASS", sha(last.encode())
    if "Error: " in last and "Failed to load" in last:
        return "INFRA_FAILURE", sha(last.encode())
    return "FAIL", sha(last.encode())


def score(outcomes: list[dict], product_baseline: str, holdout_baseline: str) -> dict:
    must(product_baseline == holdout_baseline == "PASS", "BASELINE_NOT_GREEN")
    for row in outcomes:
        must(row.get("original") in {"PASS", "FAIL", "TIMEOUT", "INFRA_FAILURE", "INVALID_MUTANT"}
             and row.get("with_holdout") in {"PASS", "FAIL", "TIMEOUT", "INFRA_FAILURE", "INVALID_MUTANT"},
             "INVALID_TEST_RESULT")
    must(not any(r["original"] == "FAIL" and r["with_holdout"] == "PASS"
                 for r in outcomes), "INCONSISTENT_TEST_OUTCOME")
    valid = [r for r in outcomes
             if r["original"] in {"PASS", "FAIL"} and r["with_holdout"] in {"PASS", "FAIL"}]
    killed_product = [r["id"] for r in valid if r["original"] == "FAIL"]
    extra = [r["id"] for r in valid if r["original"] == "PASS" and r["with_holdout"] == "FAIL"]
    survivors = [r["id"] for r in valid if r["original"] == r["with_holdout"] == "PASS"]
    unqualified = [r["id"] for r in outcomes if r not in valid]
    return {
        "source_mutants_attempted": len(outcomes),
        "valid_mutants": len(valid),
        "caught_existing_tests": len(killed_product),
        "additional_caught_by_independent_holdout": len(extra),
        "survived_both": len(survivors),
        "not_qualified": len(unqualified),
        "existing_mutation_score": round(len(killed_product) / len(valid), 4) if valid else None,
        "combined_mutation_score": round((len(killed_product) + len(extra)) / len(valid), 4)
        if valid else None,
        "independent_only_kills": extra,
        "original_suite_kills": killed_product,
        "survivors": survivors,
        "unqualified": unqualified,
    }


def run(product_root: Path, expected_head: str, *, output: Path) -> dict:
    must(bool(HEAD_PATTERN.fullmatch(expected_head)), "BAD_EXPECTED_HEAD")
    must(not output.exists(), "REFUSE_OVERWRITE_EXISTING_EVIDENCE")
    raw = obtain_snapshot(product_root, expected_head)
    must(HOLDOUT_SOURCE.is_file(), "MISSING_INDEPENDENT_QA_HOLDOUT")
    holdout_data = HOLDOUT_SOURCE.read_bytes()
    with tempfile.TemporaryDirectory(prefix="parser-studio-source-mutation-") as temporary:
        root = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as t:
            t.extractall(root, filter="data")
        target = root / SOURCE
        original = target.read_text(encoding="utf-8")
        for label, before, after in MUTANTS:
            must(original.count(before) == 1 and before != after, "MUTANT_PATTERN_MISMATCH:" + label)
        must((root / PRODUCT_TEST).is_file(), "MISSING_PRODUCT_TEST")
        (root / HOLDOUT_TEST).write_bytes(holdout_data)

        dependency = product_root / "node_modules"
        must((dependency / ".bin" / "vitest").is_file(), "MISSING_PINNED_DEPENDENCY")
        os.symlink(str(dependency.resolve()), root / "node_modules", target_is_directory=True)
        control, control_digest = execute_test(root, holdout=False)
        extra_control, extra_digest = execute_test(root, holdout=True)
        must(control == extra_control == "PASS", "BASELINE_NOT_GREEN")
        rows: list[dict] = []
        must(typescript_parses(root, target), "ORIGINAL_SOURCE_INVALID")
        for label, before, after in MUTANTS:
            mutated = original.replace(before, after, 1)
            target.write_text(mutated, encoding="utf-8")
            if not typescript_parses(root, target):
                rows.append({"id": label, "mutant_sha256": sha(mutated.encode()),
                             "original": "INVALID_MUTANT", "with_holdout": "INVALID_MUTANT"})
                target.write_text(original, encoding="utf-8")
                continue
            before_status, before_digest = execute_test(root, holdout=False)
            after_status, after_digest = execute_test(root, holdout=True)
            rows.append({
                "id": label,
                "mutant_sha256": sha(mutated.encode()),
                "original": before_status,
                "with_holdout": after_status,
                "original_log_tail_sha256": before_digest,
                "with_holdout_log_tail_sha256": after_digest,
            })
            target.write_text(original, encoding="utf-8")
    statistics = score(rows, control, extra_control)
    record = {
        "kind": "parser-studio-real-source-mutation-pilot",
        "authority": "T0_ADVISORY_ONLY",
        "human_equivalent_release_gate": False,
        "source_modified_outside_sandbox": False,
        "source_repository": "xdr-labs/parser-studio",
        "source_head": expected_head,
        "product_source_path": str(SOURCE),
        "product_source_sha256": sha(original.encode()),
        "independent_holdout_sha256": sha(holdout_data),
        "git_archive_sha256": sha(raw),
        "baseline_product_tests": control,
        "baseline_with_independent_qa": extra_control,
        "baseline_product_log_tail_sha256": control_digest,
        "baseline_combined_log_tail_sha256": extra_digest,
        "metrics": statistics,
        "mutants": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps({"kind": record["kind"], "source_head": expected_head,
                      "metrics": statistics, "authority": "T0_ADVISORY_ONLY"}, sort_keys=True))
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-root", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        record = run(args.product_root, args.expected_head, output=args.out)
        return 2 if record["metrics"]["not_qualified"] else 0
    except (Unsafe, subprocess.CalledProcessError, OSError, tarfile.TarError) as exc:
        print(json.dumps({"kind": "parser-studio-real-source-mutation-pilot",
                          "status": "BLOCK",
                          "reason": str(exc) if isinstance(exc, Unsafe) else type(exc).__name__,
                          "authority": "T0_ADVISORY_ONLY"}, sort_keys=True))
        return 3


if __name__ == "__main__":
    sys.exit(main())
