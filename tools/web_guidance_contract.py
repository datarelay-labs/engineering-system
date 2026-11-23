#!/usr/bin/env python3
"""Normalize optional Modern Web Guidance + axe observations without granting a gate.

This program is a pure, bounded readback of artifacts created on a pinned
nonproduction product checkout. It never installs packages, browses, or writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

SHA = re.compile(r"^[0-9a-f]{40}$")
IDENT = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
MAX_BYTES = 1024 * 1024
IMPACT = {"critical", "serious", "moderate", "minor"}
RULE = re.compile(r"^[a-z][a-z0-9-]{1,100}$")


class EvidenceBlocked(ValueError):
    pass


def read_json(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise EvidenceBlocked("UNTRUSTED_OR_OVERSIZED_EVIDENCE")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceBlocked("EVIDENCE_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise EvidenceBlocked("EVIDENCE_OBJECT_REQUIRED")
    return value, hashlib.sha256(raw).hexdigest()


def exact_checkout(repo: Path, expected_sha: str) -> None:
    if not SHA.fullmatch(expected_sha) or not repo.is_dir() or repo.is_symlink():
        raise EvidenceBlocked("CHECKOUT_INVALID")
    try:
        p = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, timeout=4, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceBlocked("CHECKOUT_UNAVAILABLE") from exc
    if p.returncode or p.stdout.strip() != expected_sha:
        raise EvidenceBlocked("SOURCE_HEAD_MISMATCH")
    for arguments in (("--show-toplevel",),):
        result = subprocess.run(["git", "-C", str(repo), "rev-parse", *arguments],
                                capture_output=True, text=True, timeout=4)
        if result.returncode or Path(result.stdout.strip()).resolve() != repo.resolve():
            raise EvidenceBlocked("NOT_REPOSITORY_ROOT")
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True, text=True, timeout=7)
    if status.returncode or status.stdout.strip():
        raise EvidenceBlocked("WORKTREE_DIRTY")


def scoped_project(repo: Path) -> str:
    meta = repo / ".engineering" / "project.yaml"
    if meta.is_symlink() or not meta.is_file() or meta.stat().st_size > 65536:
        raise EvidenceBlocked("PROJECT_POLICY_MISSING")
    try:
        data = yaml.safe_load(meta.read_text("utf-8"))
    except (ValueError, UnicodeError, yaml.YAMLError) as exc:
        raise EvidenceBlocked("PROJECT_POLICY_INVALID") from exc
    project = data.get("project") if isinstance(data, dict) else None
    if not isinstance(project, dict):
        raise EvidenceBlocked("PROJECT_POLICY_INVALID")
    if project.get("user_facing") is not True or project.get("primary_user_surface") not in ("web", "browser"):
        raise EvidenceBlocked("NOT_A_WEB_CONSUMER")
    name = project.get("name")
    if not isinstance(name, str) or not IDENT.fullmatch(name):
        raise EvidenceBlocked("PROJECT_NAME_INVALID")
    return name


def bounded_int(value: Any, upper: int) -> int:
    if type(value) is not int or value < 0 or value > upper:
        raise EvidenceBlocked("COUNT_INVALID")
    return value


def summarize(
    legacy: dict[str, Any], browser: dict[str, Any] | None, *,
    source_sha: str, project: str, legacy_digest: str,
    browser_digest: str | None
) -> dict[str, Any]:
    if not SHA.fullmatch(source_sha) or not IDENT.fullmatch(project):
        raise EvidenceBlocked("SOURCE_INVALID")
    total = bounded_int(legacy.get("totalFindings"), 5000)
    guides = legacy.get("guides")
    if not isinstance(guides, list) or len(guides) > 100:
        raise EvidenceBlocked("GUIDES_INVALID")
    modernization: dict[str, int] = {}
    observed = 0
    for guide in guides:
        if not isinstance(guide, dict) or not RULE.fullmatch(str(guide.get("guideId", ""))):
            raise EvidenceBlocked("GUIDE_INVALID")
        rules = guide.get("rules")
        if not isinstance(rules, list) or len(rules) > 100:
            raise EvidenceBlocked("RULES_INVALID")
        for rule in rules:
            if not isinstance(rule, dict):
                raise EvidenceBlocked("RULE_INVALID")
            rule_id = rule.get("ruleId")
            count = bounded_int(rule.get("count"), 5000)
            findings = rule.get("findings")
            if not isinstance(rule_id, str) or len(rule_id) > 140 or not rule_id.startswith(guide["guideId"] + "/"):
                raise EvidenceBlocked("RULE_ID_INVALID")
            if not isinstance(findings, list) or len(findings) != count:
                raise EvidenceBlocked("FINDING_COUNT_INCONSISTENT")
            for finding in findings:
                if not isinstance(finding, dict):
                    raise EvidenceBlocked("FINDING_INVALID")
                file = finding.get("file")
                if not isinstance(file, str) or file.startswith("/") or ".." in Path(file).parts or len(file) > 300:
                    raise EvidenceBlocked("FINDING_PATH_UNSAFE")
                bounded_int(finding.get("line"), 10_000_000)
            if rule_id in modernization:
                raise EvidenceBlocked("DUPLICATE_RULE")
            modernization[rule_id] = count
            observed += count
    if observed != total:
        raise EvidenceBlocked("TOTAL_COUNT_INCONSISTENT")
    issues: dict[str, dict[str, int | str]] = {}
    a11y_result = "NOT_TESTED"
    if browser is not None:
        if browser.get("receipt") != "ES_MODERN_WEB_REAL_CONSUMER_A11Y_V1":
            raise EvidenceBlocked("BROWSER_RECEIPT_INVALID")
        if browser.get("source_head") != source_sha:
            raise EvidenceBlocked("BROWSER_SOURCE_MISMATCH")
        if browser.get("external_navigation") != "BLOCKED":
            raise EvidenceBlocked("BROWSER_EGRESS_UNVERIFIED")
        if browser.get("full_user_e2e_gate") != "NOT_ESTABLISHED":
            raise EvidenceBlocked("BROWSER_GATE_INFLATED")
        violations = browser.get("violations")
        if not isinstance(violations, list) or len(violations) > 100:
            raise EvidenceBlocked("BROWSER_VIOLATIONS_INVALID")
        for item in violations:
            if not isinstance(item, dict):
                raise EvidenceBlocked("BROWSER_RULE_INVALID")
            code = item.get("id")
            if not isinstance(code, str) or not RULE.fullmatch(code) or code in issues or item.get("impact") not in IMPACT:
                raise EvidenceBlocked("BROWSER_RULE_INVALID")
            issues[code] = {"impact": item["impact"], "nodes": bounded_int(item.get("nodes"), 10000)}
        bounded_int(browser.get("passes"), 500)
        a11y_result = "ISSUES_OBSERVED" if issues else "NO_ISSUES_IN_AUTOMATED_SCOPE"
    return {
        "schema": "engineering.optional-web-guidance.v1",
        "project": project,
        "source_head": source_sha,
        "scanner_input_attestation": "CALLER_SUPPLIED_NOT_INDEPENDENTLY_ATTESTED",
        "browser_egress_attestation": "CALLER_SUPPLIED_GUARD_NOT_OS_ATTESTED",
        "scanner": "modern-web-guidance@1.0.1",
        "scanner_evidence_sha256": legacy_digest,
        "modernization_rule_counts": dict(sorted(modernization.items())),
        "modernization_suggestion_count": observed,
        "modernization_authority": "REVIEW_ONLY_NO_AUTOFIX",
        "browser_analyzer": "@axe-core/playwright@4.14.0" if browser is not None else None,
        "browser_evidence_sha256": browser_digest,
        "automated_wcag_status": a11y_result,
        "automated_wcag_rule_results": dict(sorted(issues.items())),
        "automated_wcag_violating_nodes": sum(int(v["nodes"]) for v in issues.values()),
        "full_wcag_conformance": "NOT_ESTABLISHED",
        "full_user_e2e": "NOT_ESTABLISHED",
        "canonical_release_gate": "UNCHANGED",
        "mutation_authority": "NO",
        "external_upload": "NONE",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--legacy-scan", required=True, type=Path)
    parser.add_argument("--axe-receipt", type=Path)
    args = parser.parse_args()
    try:
        exact_checkout(args.repo_root, args.source_sha)
        project = scoped_project(args.repo_root)
        legacy, digest = read_json(args.legacy_scan)
        browser, browser_digest = read_json(args.axe_receipt) if args.axe_receipt else (None, None)
        result = summarize(
            legacy, browser, source_sha=args.source_sha, project=project,
            legacy_digest=digest, browser_digest=browser_digest
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "BLOCK", "reason": str(exc)}, sort_keys=True))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
