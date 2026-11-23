#!/usr/bin/env python3
"""Read-only cross-project GitHub adoption census.

Checks main HEAD, canonical policy presence, and observed *exact-head* GitHub
workflow runs; never interprets an isolated successful workflow as release,
product Full User E2E, or owner permission. No API mutation or source upload.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from typing import Any

import yaml

REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_REPOS = 12


class AuditBlocked(ValueError):
    pass


def github(path: str, *, missing_ok: bool = False) -> dict[str, Any] | None:
    try:
        p = subprocess.run(["gh", "api", path], capture_output=True,
                           text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditBlocked("AUTHENTICATED_GITHUB_UNAVAILABLE") from exc
    if p.returncode:
        if missing_ok and "404" in p.stderr and "Not Found" in p.stderr:
            return None
        raise AuditBlocked("GITHUB_READ_FAILED")
    try:
        value = json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        raise AuditBlocked("GITHUB_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise AuditBlocked("GITHUB_OBJECT_EXPECTED")
    return value


def policy(contents: dict[str, Any] | None) -> dict[str, Any] | None:
    if contents is None:
        return None
    if contents.get("type") != "file":
        raise AuditBlocked("POLICY_NOT_A_FILE")
    raw = contents.get("content")
    if not isinstance(raw, str) or len(raw) > 96000:
        raise AuditBlocked("POLICY_INVALID_SIZE")
    try:
        parsed = yaml.safe_load(base64.b64decode("".join(raw.split()), validate=True))
    except (ValueError, UnicodeError, yaml.YAMLError) as exc:
        raise AuditBlocked("POLICY_INVALID") from exc
    if not isinstance(parsed, dict):
        raise AuditBlocked("POLICY_INVALID")
    es = parsed.get("engineering_system")
    if not isinstance(es, dict):
        raise AuditBlocked("ENGINEERING_POLICY_ABSENT")
    # Do not output private project metadata or source contents.
    fields = ("version", "policy_epoch", "governance_epoch", "mode")
    return {k: es.get(k) for k in fields}


def summarize(repo: str, sha: str, *, agents_present: bool,
              es: dict[str, Any] | None, runs: list[dict[str, Any]],
              origin: str) -> dict[str, Any]:
    if (not REPO.fullmatch(repo) or any(part in (".", "..") for part in repo.split("/"))
            or not SHA.fullmatch(sha) or type(agents_present) is not bool):
        raise AuditBlocked("SOURCE_IDENTITY_INVALID")
    if not isinstance(runs, list) or len(runs) > 100:
        raise AuditBlocked("CI_PAGE_UNBOUNDED")
    selected = []
    for r in runs:
        if not isinstance(r, dict):
            raise AuditBlocked("CI_ENTRY_INVALID")
        if r.get("head_sha") != sha:
            continue
        name = r.get("name")
        if not isinstance(name, str) or len(name) > 160:
            raise AuditBlocked("CI_WORKFLOW_NAME_INVALID")
        result = r.get("conclusion")
        status = r.get("status")
        if result not in (None, "success", "failure", "cancelled", "skipped",
                          "timed_out", "action_required", "neutral", "stale"):
            raise AuditBlocked("CI_RESULT_INVALID")
        if status not in ("queued", "waiting", "in_progress", "completed", "requested", "pending"):
            raise AuditBlocked("CI_STATUS_INVALID")
        selected.append({"name": name, "status": status, "conclusion": result})
    declared = bool(agents_present and es is not None and es.get("mode") == "adopted")
    if not agents_present or es is None:
        policy_state = "POLICY_MISSING_ON_MAIN"
    elif declared:
        policy_state = "DECLARED_ADOPTED_NOT_RUNTIME_ATTESTED"
    elif repo == "datarelay-labs/engineering-system" and es.get("mode") == "canonical":
        policy_state = "CANONICAL_SOURCE_DECLARED"
    else:
        policy_state = "NONCANONICAL_DECLARATION_REVIEW"
    if any(x["conclusion"] in ("failure", "timed_out", "action_required") for x in selected):
        ci_state = "FAILURES_OBSERVED_AT_EXACT_HEAD"
    elif not selected:
        ci_state = "NO_EXACT_HEAD_RUN_IN_QUERIED_WINDOW"
    elif any(x["status"] != "completed" for x in selected):
        ci_state = "WORKFLOWS_PENDING_AT_EXACT_HEAD"
    else:
        ci_state = "OBSERVED_WORKFLOWS_WITHOUT_GATE_COMPLETENESS"
    return {
        "repo": repo, "main_head": sha,
        "agents_present": agents_present,
        "engineering_system": es,
        "adoption": policy_state,
        "ci_observation": ci_state,
        "exact_head_workflow_runs": selected,
        "workflow_window": "RECENT_100_MAIN_RUNS",
        "source_provenance": origin,
        "adoption_contract_checked_locally": False,
        "user_e2e": "NOT_VERIFIED",
        "release_authority": "NO",
    }


def live(repo: str) -> dict[str, Any]:
    if not REPO.fullmatch(repo) or any(part in (".", "..") for part in repo.split("/")):
        raise AuditBlocked("REPO_INVALID")
    branch = github(f"repos/{repo}/branches/main")
    sha = ((branch or {}).get("commit") or {}).get("sha")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise AuditBlocked("MAIN_SHA_INVALID")
    agents = github(f"repos/{repo}/contents/AGENTS.md?ref=main", missing_ok=True)
    project = github(f"repos/{repo}/contents/.engineering/project.yaml?ref=main", missing_ok=True)
    history = github(f"repos/{repo}/actions/runs?branch=main&per_page=100")
    if not isinstance((history or {}).get("workflow_runs"), list):
        raise AuditBlocked("CI_HISTORY_INVALID")
    return summarize(repo, sha, agents_present=agents is not None, es=policy(project),
                     runs=history["workflow_runs"],
                     origin="AUTHENTICATED_GITHUB_READ_ONLY")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", action="append", required=True)
    args = p.parse_args()
    try:
        if not 1 <= len(args.repo) <= MAX_REPOS or len(set(args.repo)) != len(args.repo):
            raise AuditBlocked("REPO_SET_INVALID")
        rows = [live(repo) for repo in args.repo]
    except AuditBlocked as exc:
        print(json.dumps({"status": "BLOCK", "reason": str(exc)}))
        return 3
    print(json.dumps({
        "schema": "engineering.cross-project-adoption-readonly.v1",
        "authority": "T0_ADVISORY_ONLY",
        "canonical_release_gate": "UNCHANGED",
        "all_product_user_e2e": "NOT_VERIFIED",
        "repository_count": len(rows),
        "rows": rows,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
