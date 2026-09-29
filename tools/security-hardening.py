#!/usr/bin/env python3
"""Bounded desired-state / dry-run / safe-apply gate for repository security hardening.

Observed state comes only from ``tools/security-profile.py`` (read-only audit).
Default mode is dry-run. Execute requires current evidence, an explicit allowed
control set, and a mutator; unsupported or ambiguous controls fail closed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "security-hardening-plan.schema.json"
PROFILE_TOOL = ROOT / "tools" / "security-profile.py"

APPLYABLE_CONTROLS = frozenset(
    {
        "secret_scanning",
        "push_protection",
        "dependabot_security_updates",
        "codeql_or_sast",
    }
)
UNSUPPORTED_CONTROLS = frozenset(
    {
        "repository_visibility",
        "sensitive_action_pin",
        "ordinary_action_pin",
        "privileged_tool_provenance",
        "ruleset_protection",
        "actions_policy",
        "review_thread_protection",
    }
)
DESIRED_VALUE = {
    "secret_scanning": "enabled",
    "push_protection": "enabled",
    "dependabot_security_updates": "enabled",
    "codeql_or_sast": "configured",
}
COMPLIANT_STATES = frozenset(
    {
        "REQUIRED_PASS",
        "RECOMMENDED_PASS",
        "EQUIVALENT_EXTERNAL",
        "DEFERRED",
        "NOT_APPLICABLE",
    }
)
GAP_STATES = frozenset({"REQUIRED_FAIL", "RECOMMENDED_GAP"})
AMBIGUOUS_STATES = frozenset({"UNAVAILABLE", "UNKNOWN"})


class HardeningError(Exception):
    pass


class Mutator(Protocol):
    def apply(self, repository: str, mutations: list[dict[str, str]]) -> list[dict[str, str]]:
        ...


def load_profile_module():
    spec = importlib.util.spec_from_file_location("security_profile", PROFILE_TOOL)
    if spec is None or spec.loader is None:
        raise HardeningError("security-profile.py is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROFILE = load_profile_module()


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def command_names() -> tuple[str, ...]:
    return ("plan", "apply")


def normalize_allowed(raw: list[str] | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = item.strip()
        if not name or name in seen:
            continue
        if len(name) > 80:
            raise HardeningError(f"allowed control exceeds length bound: {name[:40]}")
        seen.add(name)
        out.append(name)
    return out


def requirement_from_observed(profile: str, control_id: str, observed_state: str) -> str:
    if observed_state == "DEFERRED":
        return "DEFERRED"
    if observed_state == "NOT_APPLICABLE":
        return "NOT_APPLICABLE"
    if profile == "NEEDS_INPUT" or observed_state == "UNKNOWN":
        return "UNKNOWN"
    if control_id == "privileged_tool_provenance":
        if observed_state in {"REQUIRED_PASS", "REQUIRED_FAIL"}:
            return "REQUIRED"
        if observed_state == "UNAVAILABLE":
            return PROFILE.requirement_for(profile, control_id, privileged_declared=False)
        return PROFILE.requirement_for(profile, control_id, privileged_declared=True)
    return PROFILE.requirement_for(profile, control_id, privileged_declared=False)


def decide_control(profile: str, item: dict[str, str]) -> dict[str, Any]:
    control_id = item["id"]
    observed = item["state"]
    detail = item.get("detail", "")[:400]
    requirement = requirement_from_observed(profile, control_id, observed)
    applyable = control_id in APPLYABLE_CONTROLS

    if control_id in UNSUPPORTED_CONTROLS and control_id not in APPLYABLE_CONTROLS:
        if observed in GAP_STATES:
            return {
                "id": control_id,
                "requirement": requirement,
                "observed_state": observed,
                "desired": "blocked",
                "action": "BLOCK",
                "applyable": False,
                "detail": f"unsupported apply surface; observed={observed}",
            }
        if observed in AMBIGUOUS_STATES:
            return {
                "id": control_id,
                "requirement": requirement,
                "observed_state": observed,
                "desired": "blocked",
                "action": "BLOCK",
                "applyable": False,
                "detail": f"ambiguous unsupported control; observed={observed}",
            }
        return {
            "id": control_id,
            "requirement": requirement,
            "observed_state": observed,
            "desired": "unchanged",
            "action": "NOOP",
            "applyable": False,
            "detail": f"non-applyable control remains {observed}",
        }

    if requirement in {"DEFERRED", "NOT_APPLICABLE"}:
        desired = "deferred" if requirement == "DEFERRED" else "not_applicable"
        return {
            "id": control_id,
            "requirement": requirement,
            "observed_state": observed,
            "desired": desired,
            "action": "NOOP",
            "applyable": applyable,
            "detail": f"profile keeps control {requirement.lower().replace('_', '-')}",
        }

    if requirement == "UNKNOWN" or profile == "NEEDS_INPUT":
        return {
            "id": control_id,
            "requirement": "UNKNOWN",
            "observed_state": observed,
            "desired": "blocked",
            "action": "BLOCK",
            "applyable": applyable,
            "detail": "profile is NEEDS_INPUT; refuse desired-state guess",
        }

    if observed in COMPLIANT_STATES:
        return {
            "id": control_id,
            "requirement": requirement,
            "observed_state": observed,
            "desired": "unchanged",
            "action": "NOOP",
            "applyable": applyable,
            "detail": f"already compliant ({observed})",
        }

    if observed in AMBIGUOUS_STATES:
        return {
            "id": control_id,
            "requirement": requirement,
            "observed_state": observed,
            "desired": "blocked",
            "action": "BLOCK",
            "applyable": applyable,
            "detail": f"ambiguous observed state ({observed}): {detail}"[:400],
        }

    if observed in GAP_STATES:
        if not applyable:
            return {
                "id": control_id,
                "requirement": requirement,
                "observed_state": observed,
                "desired": "blocked",
                "action": "BLOCK",
                "applyable": False,
                "detail": f"gap on non-applyable control ({observed})",
            }
        return {
            "id": control_id,
            "requirement": requirement,
            "observed_state": observed,
            "desired": DESIRED_VALUE[control_id],
            "action": "ENABLE",
            "applyable": True,
            "detail": f"enable GitHub-native control from {observed}",
        }

    return {
        "id": control_id,
        "requirement": requirement,
        "observed_state": observed,
        "desired": "blocked",
        "action": "BLOCK",
        "applyable": applyable,
        "detail": f"unrecognized observed state {observed}",
    }


def plan_digest_for(payload: dict[str, Any]) -> str:
    material = {
        "repository": payload["repository"],
        "profile": payload["profile"],
        "binding": payload["binding"],
        "allowed_controls": payload["allowed_controls"],
        "controls": payload["controls"],
        "planned_mutations": payload["planned_mutations"],
        "blockers": payload["blockers"],
        "eligible_apply": payload["eligible_apply"],
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate_eligibility(
    *,
    profile: str,
    binding: str,
    repository: str | None,
    controls: list[dict[str, Any]],
    allowed: list[str],
) -> tuple[bool, list[str], list[dict[str, str]]]:
    blockers: list[str] = []
    if profile == "NEEDS_INPUT":
        blockers.append("profile is NEEDS_INPUT")
    if repository is None:
        blockers.append("repository identity is unavailable")
    for name in allowed:
        if name in UNSUPPORTED_CONTROLS or name not in APPLYABLE_CONTROLS:
            blockers.append(f"allowed control is unsupported for apply: {name}")
    if allowed and binding != "ok":
        blockers.append(f"fixture binding is {binding}; GitHub settings apply requires ok")

    by_id = {item["id"]: item for item in controls}
    planned: list[dict[str, str]] = []
    for name in allowed:
        item = by_id.get(name)
        if item is None:
            blockers.append(f"allowed control missing from audit: {name}")
            continue
        if item["action"] == "BLOCK":
            blockers.append(f"allowed control is blocked: {name}")
            continue
        if item["action"] == "ENABLE":
            planned.append(
                {
                    "id": name,
                    "desired": item["desired"],
                    "detail": item["detail"][:400],
                }
            )
    # Fail closed when required applyable gaps exist and were not allowed.
    for item in controls:
        if item["id"] not in APPLYABLE_CONTROLS:
            continue
        if item["requirement"] != "REQUIRED":
            continue
        if item["action"] != "ENABLE":
            continue
        if item["id"] not in allowed:
            blockers.append(f"required applyable gap is not in allowed set: {item['id']}")

    # Ambiguous/unsupported required gaps always block eligibility.
    for item in controls:
        if item["action"] != "BLOCK":
            continue
        if item["requirement"] == "REQUIRED" or item["observed_state"] in AMBIGUOUS_STATES:
            if item["id"] in APPLYABLE_CONTROLS or item["observed_state"] in AMBIGUOUS_STATES:
                blockers.append(f"blocking control prevents apply: {item['id']}")

    # Deduplicate blockers while preserving order.
    deduped: list[str] = []
    seen: set[str] = set()
    for item in blockers:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return (not deduped, deduped, planned)


def build_plan(
    root: Path,
    fixture: dict[str, Any] | None,
    *,
    allowed_controls: list[str] | None = None,
    mode: str = "DRY_RUN",
) -> dict[str, Any]:
    if mode not in {"DRY_RUN", "EXECUTE"}:
        raise HardeningError(f"unknown mode {mode}")
    allowed = normalize_allowed(allowed_controls)
    binding = PROFILE.github_fixture_binding(root, fixture)
    report = PROFILE.build_report(root, fixture)
    repository = PROFILE.local_repository_identity(root)
    controls = [decide_control(report["profile"], item) for item in report["controls"]]
    eligible, blockers, planned = evaluate_eligibility(
        profile=report["profile"],
        binding=binding,
        repository=repository,
        controls=controls,
        allowed=allowed,
    )
    mutation = "NONE"
    if mode == "DRY_RUN" and planned:
        mutation = "PLANNED"
    payload = {
        "schema_version": 1,
        "kind": "security-hardening-plan",
        "repository": repository,
        "profile": report["profile"],
        "binding": binding,
        "mode": mode,
        "allowed_controls": allowed,
        "controls": controls,
        "planned_mutations": planned,
        "blockers": blockers,
        "eligible_apply": eligible,
        "mutation": mutation,
        "network": "NONE",
    }
    payload["plan_digest"] = plan_digest_for(payload)
    Draft202012Validator(load_schema()).validate(payload)
    return payload


class RecordingMutator:
    """Test/local mutator that records intended GitHub setting changes without network."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def apply(self, repository: str, mutations: list[dict[str, str]]) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        for item in mutations:
            self.calls.append({"repository": repository, **item})
            results.append(
                {
                    "id": item["id"],
                    "status": "APPLIED",
                    "detail": f"recorded desired={item['desired']} for {repository}",
                }
            )
        return results


class GhApiMutator:
    """Bounded GitHub settings mutator. Never logs credential values."""

    def apply(self, repository: str, mutations: list[dict[str, str]]) -> list[dict[str, str]]:
        if PROFILE.REPO_RE.fullmatch(repository) is None:
            raise HardeningError("repository identity is malformed")
        # Validate the full batch before any network mutation to avoid partial apply.
        bodies: list[tuple[dict[str, str], dict[str, Any]]] = []
        for item in mutations:
            control_id = item["id"]
            if control_id not in APPLYABLE_CONTROLS:
                raise HardeningError(f"refusing unsupported mutation: {control_id}")
            bodies.append((item, _github_settings_patch(control_id, item["desired"])))
        results: list[dict[str, str]] = []
        for item, body in bodies:
            control_id = item["id"]
            completed = subprocess.run(
                [
                    "gh",
                    "api",
                    "-X",
                    "PATCH",
                    f"repos/{repository}",
                    "--input",
                    "-",
                ],
                input=json.dumps(body),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "gh api failed").strip()
                detail = detail.splitlines()[0][:400] if detail else "gh api failed"
                # Never retain token-looking fragments.
                detail = detail.replace("Bearer ", "").replace("token ", "")
                results.append({"id": control_id, "status": "FAILED", "detail": detail})
                raise HardeningError(f"mutation failed for {control_id}: {detail}")
            results.append(
                {
                    "id": control_id,
                    "status": "APPLIED",
                    "detail": f"github setting set to {item['desired']}",
                }
            )
        return results


def _github_settings_patch(control_id: str, desired: str) -> dict[str, Any]:
    if control_id == "secret_scanning" and desired == "enabled":
        return {"security_and_analysis": {"secret_scanning": {"status": "enabled"}}}
    if control_id == "push_protection" and desired == "enabled":
        return {
            "security_and_analysis": {
                "secret_scanning_push_protection": {"status": "enabled"}
            }
        }
    if control_id == "dependabot_security_updates" and desired == "enabled":
        return {
            "security_and_analysis": {
                "dependabot_security_updates": {"status": "enabled"}
            }
        }
    if control_id == "codeql_or_sast" and desired == "configured":
        # Default setup enablement is a separate endpoint; refuse silent substitute.
        raise HardeningError(
            "codeql_or_sast execute requires explicit code-scanning default-setup path"
        )
    raise HardeningError(f"unsupported desired value for {control_id}: {desired}")


def apply_plan(
    root: Path,
    fixture: dict[str, Any] | None,
    *,
    allowed_controls: list[str] | None,
    execute: bool,
    expect_plan_digest: str | None = None,
    mutator: Mutator | None = None,
) -> dict[str, Any]:
    mode = "EXECUTE" if execute else "DRY_RUN"
    plan = build_plan(root, fixture, allowed_controls=allowed_controls, mode=mode)
    if expect_plan_digest:
        # Recompute dry-run digest material and compare caller expectation.
        baseline = build_plan(
            root, fixture, allowed_controls=allowed_controls, mode="DRY_RUN"
        )
        if expect_plan_digest != baseline["plan_digest"]:
            raise HardeningError(
                "STALE_PLAN: current desired-state digest does not match expect-plan-digest"
            )
        if plan["plan_digest"] != baseline["plan_digest"] and mode == "DRY_RUN":
            raise HardeningError("internal plan digest mismatch")

    if not execute:
        return plan

    if not plan["eligible_apply"]:
        plan["mutation"] = "NONE"
        plan["network"] = "NONE"
        plan["apply_result"] = []
        Draft202012Validator(load_schema()).validate(plan)
        return plan

    if plan["repository"] is None:
        raise HardeningError("repository identity is unavailable")

    active = mutator
    network = "NONE"
    if active is None:
        active = GhApiMutator()
        network = "GITHUB_SETTINGS"
    elif isinstance(active, GhApiMutator):
        network = "GITHUB_SETTINGS"

    if not plan["planned_mutations"]:
        plan["mutation"] = "NONE"
        plan["network"] = "NONE"
        plan["apply_result"] = []
        Draft202012Validator(load_schema()).validate(plan)
        return plan

    results = active.apply(plan["repository"], plan["planned_mutations"])
    plan["apply_result"] = results
    plan["mutation"] = "APPLIED"
    plan["network"] = network
    # Digest stays bound to desired-state material, not apply_result.
    Draft202012Validator(load_schema()).validate(plan)
    return plan


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Desired-state / dry-run / safe-apply gate for security hardening"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Compute desired-state diff (always dry-run)")
    plan.add_argument("--root", required=True)
    plan.add_argument("--github-fixture")
    plan.add_argument("--allowed-control", action="append", default=[])

    apply = sub.add_parser("apply", help="Evaluate apply eligibility; dry-run unless --execute")
    apply.add_argument("--root", required=True)
    apply.add_argument("--github-fixture")
    apply.add_argument("--allowed-control", action="append", default=[], required=False)
    apply.add_argument("--execute", action="store_true", default=False)
    apply.add_argument("--expect-plan-digest")
    apply.add_argument(
        "--mutation-backend",
        choices=("github", "record"),
        default="github",
        help="github uses gh api; record is network-free and for tests/local rehearsal",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print("security hardening root is not a directory", file=sys.stderr)
        return 1
    fixture = None
    if getattr(args, "github_fixture", None):
        try:
            fixture = PROFILE.load_fixture(Path(args.github_fixture))
        except PROFILE.ProfileError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    try:
        if args.command == "plan":
            document = build_plan(
                root,
                fixture,
                allowed_controls=args.allowed_control,
                mode="DRY_RUN",
            )
        else:
            mutator: Mutator | None = None
            if args.execute:
                if args.mutation_backend == "record":
                    mutator = RecordingMutator()
                else:
                    mutator = GhApiMutator()
            document = apply_plan(
                root,
                fixture,
                allowed_controls=args.allowed_control,
                execute=bool(args.execute),
                expect_plan_digest=args.expect_plan_digest,
                mutator=mutator,
            )
            if args.execute and not document["eligible_apply"]:
                print(json.dumps(document, indent=2, sort_keys=True))
                print("security hardening apply blocked", file=sys.stderr)
                return 2
    except HardeningError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
