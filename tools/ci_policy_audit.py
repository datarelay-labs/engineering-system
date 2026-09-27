#!/usr/bin/env python3
"""Network-free audit of mapped native CI triggers and expensive gates.

Reads .engineering/project.yaml, .engineering/tests.yaml, and only the
workflows listed in engineering_system.native_ci_workflows. Does not use
the network or mutate the repository.

DUPLICATE_NATIVE_CI reports overlapping unfiltered push and pull_request.
EXPENSIVE_DEFAULT_GATE reports a release_gate scenario whose effective cost
is expensive and whose triggers include affected or pr.
EXPENSIVE_PR_WORKFLOW reports a mapped workflow whose unconditional job step
run string is one simple command equivalent to an expensive release_gate
scenario command. The whole run string is matched once. Multiline and
compound shell do not correlate. Affected/pr labels are not required.
"""
from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

import yaml

# Keep in lockstep with tools/engineering-test.py scenario_cost.
COST_RANK = {"cheap": 0, "medium": 1, "expensive": 2}
LEVEL_COST = {
    "static": "cheap",
    "unit": "cheap",
    "component": "medium",
    "feature": "medium",
    "integration": "expensive",
    "ux": "expensive",
    "lifecycle": "expensive",
    "performance": "expensive",
    "e2e": "expensive",
}
_UNIVERSAL_BRANCHES = {"*", "**", "**/*"}
_PR_PUSH_ACTIVITIES = {"opened", "synchronize", "reopened"}
_SHELL_META = set(";<>|&()`")
_INTERPRETERS = {"bash", "sh"}


def scenario_cost(scenario: dict) -> str:
    explicit = str(scenario.get("cost") or "").strip()
    if explicit in COST_RANK:
        return explicit
    return LEVEL_COST.get(str(scenario.get("level") or "").strip(), "expensive")


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _trigger_block(document: object) -> object:
    """Return the workflow trigger map. PyYAML parses bare `on` as True."""
    if not isinstance(document, dict):
        return None
    if "on" in document:
        return document["on"]
    if True in document:
        return document[True]
    return None


def _event_map(trigger: object) -> dict[str, object]:
    if trigger is None:
        return {}
    if isinstance(trigger, str):
        return {trigger: None}
    if isinstance(trigger, list):
        return {str(item): None for item in trigger}
    if isinstance(trigger, dict):
        return {str(key): value for key, value in trigger.items()}
    return {}


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _pull_request_can_overlap_push(pull_request: object) -> bool:
    if not isinstance(pull_request, dict):
        return True
    if "types" not in pull_request:
        return True
    types = {item.strip() for item in _string_list(pull_request.get("types"))}
    return bool(types & _PR_PUSH_ACTIVITIES)


def _push_overlaps_unfiltered(push: object) -> bool:
    """True when push is not confined to tags or a non-universal branch set."""
    if not isinstance(push, dict):
        return True
    has_tags = "tags" in push or "tags-ignore" in push
    has_branches = "branches" in push or "branches-ignore" in push
    if has_tags and not has_branches:
        return False
    if not has_branches:
        return True
    if "branches" not in push:
        return True
    patterns = [item.strip() for item in _string_list(push.get("branches"))]
    if not patterns:
        return True
    return any(item in _UNIVERSAL_BRANCHES for item in patterns)


def duplicate_native_ci(workflow_path: str, document: object) -> str | None:
    events = _event_map(_trigger_block(document))
    if "push" not in events or "pull_request" not in events:
        return None
    if not _pull_request_can_overlap_push(events["pull_request"]):
        return None
    if not _push_overlaps_unfiltered(events["push"]):
        return None
    return f"DUPLICATE_NATIVE_CI {workflow_path}"


def _triggers(scenario: dict) -> list[str]:
    raw = scenario.get("triggers")
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _shell_syntax_token(token: str) -> bool:
    if not token:
        return False
    if any(char in token for char in "$`"):
        return True
    return all(char in _SHELL_META for char in token)


def normalize_direct_invocation(command: str) -> tuple[str, tuple[str, ...]] | None:
    """Return (script, args) for one simple command, or None if not direct.

    Optional leading bash/sh and a leading ./ on the script path are
    normalized. Args are preserved. Pipelines, redirects, substitutions,
    and other compound shell syntax do not correlate.
    """
    text = command.strip()
    if not text or "\n" in text or "\\" in text:
        return None
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.commenters = ""
    try:
        raw_tokens = list(lexer)
    except ValueError:
        return None
    if not raw_tokens or any(_shell_syntax_token(token) for token in raw_tokens):
        return None
    try:
        words = shlex.split(text, posix=True)
    except ValueError:
        return None
    if not words:
        return None
    index = 0
    if words[0] in _INTERPRETERS:
        if len(words) < 2 or words[1].startswith("-"):
            return None
        index = 1
    script = words[index]
    if script.startswith("./"):
        script = script[2:]
    if not script or script.startswith("-"):
        return None
    return (script, tuple(words[index + 1 :]))


def unconditional_pull_request_invocations(
    document: object,
) -> set[tuple[str, tuple[str, ...]]]:
    """Direct commands from unconditional steps of a pull_request workflow.

    Each string run is normalized once as a whole. Newlines and shell meta
    are rejected, so multiline scripts are ignored. Workflows without
    pull_request, or whose explicit types miss opened, synchronize, and
    reopened, contribute nothing.
    """
    events = _event_map(_trigger_block(document))
    if "pull_request" not in events:
        return set()
    if not _pull_request_can_overlap_push(events["pull_request"]):
        return set()
    if not isinstance(document, dict):
        return set()
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return set()
    found: set[tuple[str, tuple[str, ...]]] = set()
    for job in jobs.values():
        if not isinstance(job, dict) or "if" in job:
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict) or "if" in step:
                continue
            run = step.get("run")
            if not isinstance(run, str):
                continue
            normalized = normalize_direct_invocation(run)
            if normalized is not None:
                found.add(normalized)
    return found


def expensive_default_gate(scenario: dict) -> str | None:
    if not isinstance(scenario, dict):
        return None
    if scenario.get("release_gate") is not True:
        return None
    if scenario_cost(scenario) != "expensive":
        return None
    triggers = _triggers(scenario)
    if not ({"affected", "pr"} & set(triggers)):
        return None
    scenario_id = str(scenario.get("id") or "<unknown>")
    rendered = "[" + ", ".join(triggers) + "]"
    command = scenario.get("command")
    command_text = command.strip() if isinstance(command, str) else ""
    return (
        f"EXPENSIVE_DEFAULT_GATE {scenario_id} cost=expensive "
        f"triggers={rendered} command={json.dumps(command_text)}"
    )


def expensive_pr_workflow(
    scenario: dict,
    workflow_path: str,
    invocations: set[tuple[str, tuple[str, ...]]],
) -> str | None:
    if not isinstance(scenario, dict):
        return None
    if scenario.get("release_gate") is not True:
        return None
    if scenario_cost(scenario) != "expensive":
        return None
    command = scenario.get("command")
    if not isinstance(command, str):
        return None
    normalized = normalize_direct_invocation(command.strip())
    if normalized is None or normalized not in invocations:
        return None
    scenario_id = str(scenario.get("id") or "<unknown>")
    rendered = "[" + ", ".join(_triggers(scenario)) + "]"
    return (
        f"EXPENSIVE_PR_WORKFLOW {workflow_path} scenario={scenario_id} "
        f"cost=expensive command={json.dumps(command.strip())} "
        f"triggers={rendered}"
    )


def _mapped_workflows(project: object) -> list[str]:
    if not isinstance(project, dict):
        return []
    engineering = project.get("engineering_system") or {}
    if not isinstance(engineering, dict):
        return []
    raw = engineering.get("native_ci_workflows") or []
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    paths: list[str] = []
    for item in raw:
        rel = str(item or "").strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        paths.append(rel)
    return paths


def _inside_root(root: Path, rel: str) -> Path | None:
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def audit(root: Path) -> list[str]:
    root = root.resolve()
    project_path = root / ".engineering" / "project.yaml"
    tests_path = root / ".engineering" / "tests.yaml"
    if not project_path.is_file():
        raise SystemExit("CI_POLICY=FAIL missing .engineering/project.yaml")
    if not tests_path.is_file():
        raise SystemExit("CI_POLICY=FAIL missing .engineering/tests.yaml")

    violations: list[str] = []
    workflow_invocations: dict[str, set[tuple[str, tuple[str, ...]]]] = {}
    project = _load_yaml(project_path) or {}
    for rel in _mapped_workflows(project):
        path = _inside_root(root, rel)
        if path is None or not path.is_file():
            continue
        document = _load_yaml(path) or {}
        finding = duplicate_native_ci(rel, document)
        if finding:
            violations.append(finding)
        workflow_invocations[rel] = unconditional_pull_request_invocations(document)

    tests = _load_yaml(tests_path) or {}
    scenarios = tests.get("scenarios") if isinstance(tests, dict) else []
    for scenario in scenarios or []:
        finding = expensive_default_gate(scenario)
        if finding:
            violations.append(finding)
        for rel, invocations in workflow_invocations.items():
            workflow_finding = expensive_pr_workflow(scenario, rel, invocations)
            if workflow_finding:
                violations.append(workflow_finding)
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit native CI duplication and expensive PR workflows")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    violations = audit(Path(args.root))
    print(f"VIOLATIONS={len(violations)}")
    for line in violations:
        print(line)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
