#!/usr/bin/env python3
"""Optional feature verification map and pure T0–T5 evidence assessor.

Maps name applicable domains and launch/drive/observe/cleanup references to
existing test, runtime, and skill/profile IDs. This tool does not create
`.engineering/verification.yaml`, does not execute project commands, and does
not merge, release, or deploy. Receipt status labels and external_digest values
are not evidence authority. The public CLI assess command reads a receipt only
and stays at T0/BLOCK. Implementer-produced output is never terminal evidence.
T1–T5 requires an in-process ``TrustedCoordinatorBoundary``. A raw dict or
parsed JSON object is not that type. A provenance string is not a trust anchor,
and the CLI does not
accept a boundary file. T4 calls ``independent_verifier.evaluate`` on that
precondition, not on receipt-minted actors. T5 sets ``AUTOMATION_ELIGIBLE`` only;
``EXTERNAL_MUTATION`` and ``DIGEST_VERIFIED`` stay NO.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
MAP_REL = Path(".engineering") / "verification.yaml"
MAP_SCHEMA_REL = Path("schemas") / "verification-contract.schema.json"
RECEIPT_SCHEMA_REL = Path("schemas") / "trust-evidence-receipt.schema.json"
BOUNDARY_SCHEMA_REL = Path("schemas") / "trust-evidence-boundary.schema.json"
LEVELS = ("T0", "T1", "T2", "T3", "T4", "T5")
ROLES = ("launch", "drive", "observe", "cleanup")
REF_FIELDS = (("tests", "test"), ("runtime", "runtime"), ("skills", "skill"), ("profiles", "profile"))
CI_ID = "exact-head"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKSTREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
EXECUTION_KEYS = frozenset(
    {
        "command",
        "commands",
        "shell",
        "argv",
        "url",
        "urls",
        "uri",
        "uris",
        "endpoint",
        "endpoints",
        "href",
        "webhook",
        "exec",
        "execute",
        "script",
        "bash",
        "powershell",
        "run",
    }
)
UNBOUNDED_KEYS = frozenset(
    {
        "log",
        "logs",
        "stdout",
        "stderr",
        "secret",
        "secrets",
        "source",
        "source_text",
        "prompt",
        "prompts",
        "transcript",
        "tool_payload",
        "tool_payloads",
    }
)
REPORT_KEYS = (
    "DECISION",
    "EXIT_CODE",
    "ACHIEVED",
    "REQUIRED",
    "ELIGIBLE",
    "COMPLETION",
    "AUTOMATION_ELIGIBLE",
    "REASON",
    "DENY_CLASS",
    "SUBJECT_HEAD",
    "INTENT_REVISION",
    "FEATURE",
    "TARGET_REPO",
    "WORKSTREAM",
    "EXTERNAL_MUTATION",
    "EXECUTES_COMMANDS",
    "DIGEST_VERIFIED",
)


class TrustInputError(Exception):
    def __init__(self, reason: str, deny_class: str):
        super().__init__(reason)
        self.reason = reason
        self.deny_class = deny_class


class TrustedCoordinatorBoundary:
    """Opaque in-process coordinator precondition.

    Constructing this object is an explicit coordinator step. Parsing or
    schema-validating a JSON object does not create it, and a raw dict is not
    this type.
    """

    __slots__ = ("_payload",)

    def __init__(self, payload: dict[str, Any]) -> None:
        if type(payload) is not dict:
            raise TrustInputError("trusted coordinator boundary payload must be a dict", "MALFORMED")
        self._payload = payload

    def payload(self) -> dict[str, Any]:
        return self._payload


def verifier_module() -> Any:
    name = "independent_verifier"
    cached = sys.modules.get(name)
    if cached is not None and hasattr(cached, "evaluate"):
        return cached
    path = Path(__file__).resolve().parent / "independent_verifier.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise TrustInputError("independent verifier is unavailable", "VERIFIER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def reject_untrusted(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).strip().lower()
            child_path = f"{path}.{key}"
            if lowered in EXECUTION_KEYS:
                raise TrustInputError(f"{child_path} is not executable authority", "EXECUTION_FORBIDDEN")
            if lowered in UNBOUNDED_KEYS:
                raise TrustInputError(f"{child_path} is not a bounded evidence reference", "UNBOUNDED_EVIDENCE")
            reject_untrusted(child, child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            reject_untrusted(child, f"{path}[{index}]")
        return
    if isinstance(value, str) and "://" in value:
        raise TrustInputError(f"{path} contains a URL", "EXECUTION_FORBIDDEN")


def load_schema(root: Path, rel: Path) -> dict[str, Any]:
    path = root / rel
    if not path.is_file():
        raise TrustInputError(f"missing {rel.as_posix()}", "MALFORMED")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TrustInputError(f"malformed schema {rel.as_posix()}", "MALFORMED") from exc
    if not isinstance(loaded, dict):
        raise TrustInputError(f"schema {rel.as_posix()} must be an object", "MALFORMED")
    return loaded


def schema_errors(schema: dict[str, Any], instance: Any) -> list[str]:
    errors = sorted(Draft202012Validator(schema).iter_errors(instance), key=lambda item: list(item.path))
    found = []
    for error in errors:
        where = ".".join(str(part) for part in error.path) or "<root>"
        found.append(f"MALFORMED {where}: {error.message.splitlines()[0]}")
    return found


def load_yaml_mapping(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TrustInputError(str(exc).splitlines()[0], "MALFORMED") from exc
    if not isinstance(loaded, dict):
        raise TrustInputError(f"{path.name} must be a mapping", "MALFORMED")
    return loaded


def project_domains(root: Path) -> set[str]:
    document = load_yaml_mapping(root / ".engineering" / "project.yaml")
    if not isinstance(document, dict):
        return set()
    raw = document.get("domains")
    if not isinstance(raw, list):
        return set()
    return {item for item in raw if isinstance(item, str)}


def catalog_ids(root: Path) -> dict[str, set[str] | None]:
    tests = load_yaml_mapping(root / ".engineering" / "tests.yaml")
    test_ids = {
        item["id"]
        for item in (tests or {}).get("scenarios") or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    runtime = load_yaml_mapping(root / ".engineering" / "runtime.yaml")
    runtime_ids = None
    if isinstance(runtime, dict):
        runtime_ids = set()
        for section in ("authorities", "capabilities"):
            block = runtime.get(section)
            if isinstance(block, dict):
                runtime_ids.update(str(key) for key in block)
    skills_doc = load_yaml_mapping(root / ".engineering" / "skills.yaml")
    skill_ids = None
    profile_ids = None
    if isinstance(skills_doc, dict):
        skill_ids = {
            item["id"]
            for item in skills_doc.get("skills") or []
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        profiles = skills_doc.get("profiles")
        profile_ids = set(str(key) for key in profiles) if isinstance(profiles, dict) else set()
    return {"tests": test_ids, "runtime": runtime_ids, "skills": skill_ids, "profiles": profile_ids}


def declared_refs(feature: dict[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    groups = {"test": [], "runtime": [], "skill": [], "profile": []}
    seen: set[tuple[str, str]] = set()
    duplicates: list[str] = []
    for role in ROLES:
        block = feature["references"][role]
        for field, authority in REF_FIELDS:
            for ref_id in block[field]:
                pair = (authority, ref_id)
                if pair in seen:
                    duplicates.append(f"{authority}:{ref_id}")
                    continue
                seen.add(pair)
                groups[authority].append(ref_id)
    return groups, duplicates


def problems(root: Path, manifest: Any) -> list[str]:
    try:
        reject_untrusted(manifest, "manifest")
    except TrustInputError as exc:
        return [f"{exc.deny_class} {exc.reason}"]
    try:
        schema = load_schema(root, MAP_SCHEMA_REL)
    except TrustInputError as exc:
        return [f"{exc.deny_class} {exc.reason}"]
    malformed = schema_errors(schema, manifest)
    if malformed:
        return malformed
    if not isinstance(manifest, dict):
        return ["MALFORMED manifest must be an object"]
    found: list[str] = []
    feature_ids: list[str] = []
    try:
        domains = project_domains(root)
        catalogs = catalog_ids(root)
    except TrustInputError as exc:
        return [f"{exc.deny_class} {exc.reason}"]
    for feature in manifest["features"]:
        feature_id = feature["id"]
        if feature_id in feature_ids:
            found.append(f"DUPLICATE_FEATURE {feature_id}")
        feature_ids.append(feature_id)
        for domain in feature["domains"]:
            if domain not in domains:
                found.append(f"UNRESOLVED_DOMAIN {domain}")
        groups, duplicates = declared_refs(feature)
        for item in duplicates:
            found.append(f"DUPLICATE_REFERENCE {feature_id} {item}")
        if not (groups["test"] or groups["skill"] or groups["profile"]):
            found.append(f"VACUOUS_GATE {feature_id}")
        if feature["minimum_level"] in {"T3", "T4", "T5"} and not groups["runtime"]:
            found.append(f"UNSATISFIABLE_RUNTIME {feature_id}")
        if feature["minimum_level"] == "T5" and feature_id not in manifest["automation_eligible"]:
            found.append(f"UNSATISFIABLE_POLICY {feature_id}")
        for test_id in groups["test"]:
            if test_id not in catalogs["tests"]:
                found.append(f"UNRESOLVED_TEST {test_id}")
        runtime_ids = catalogs["runtime"]
        for runtime_id in groups["runtime"]:
            if runtime_ids is None or runtime_id not in runtime_ids:
                found.append(f"UNRESOLVED_RUNTIME {runtime_id}")
        skill_ids = catalogs["skills"]
        for skill_id in groups["skill"]:
            if skill_ids is None or skill_id not in skill_ids:
                found.append(f"UNRESOLVED_SKILL {skill_id}")
        profile_ids = catalogs["profiles"]
        for profile_id in groups["profile"]:
            if profile_ids is None or profile_id not in profile_ids:
                found.append(f"UNRESOLVED_PROFILE {profile_id}")
    for feature_id in sorted(set(manifest["automation_eligible"]).difference(feature_ids)):
        found.append(f"UNKNOWN_FEATURE {feature_id}")
    return found


def check_contract(root: Path) -> dict[str, Any]:
    path = root / MAP_REL
    if not path.exists():
        return {"state": "ABSENT", "result": "PASS", "features": 0, "findings": []}
    try:
        manifest = load_yaml_mapping(path)
    except TrustInputError as exc:
        return {"state": "FAIL", "result": "FAIL", "features": 0, "findings": [f"{exc.deny_class} {exc.reason}"]}
    if manifest is None:
        return {"state": "FAIL", "result": "FAIL", "features": 0, "findings": ["MALFORMED verification map is unreadable"]}
    found = problems(root, manifest)
    count = len(manifest.get("features") or []) if isinstance(manifest.get("features"), list) else 0
    return {
        "state": "PRESENT" if not found else "FAIL",
        "result": "PASS" if not found else "FAIL",
        "features": count,
        "findings": found,
    }


def emit_check(report: dict[str, Any]) -> None:
    print(f"VERIFICATION_CONTRACT={report['state']}")
    print(f"RESULT={report['result']}")
    print("EXECUTES_COMMANDS=NO")
    print(f"FEATURES={report['features']}")
    for item in report["findings"]:
        print(f"FINDING={item}")


def blank_report(**fields: object) -> dict[str, str]:
    payload = {
        "DECISION": "BLOCK",
        "EXIT_CODE": "2",
        "ACHIEVED": "T0",
        "REQUIRED": "T1",
        "ELIGIBLE": "NO",
        "COMPLETION": "NO",
        "AUTOMATION_ELIGIBLE": "NO",
        "REASON": "",
        "DENY_CLASS": "NONE",
        "SUBJECT_HEAD": "",
        "INTENT_REVISION": "",
        "FEATURE": "",
        "TARGET_REPO": "",
        "WORKSTREAM": "",
        "EXTERNAL_MUTATION": "NO",
        "EXECUTES_COMMANDS": "NO",
        "DIGEST_VERIFIED": "NO",
    }
    for key, value in fields.items():
        if key in {"AUTOMATION_ELIGIBLE", "EXTERNAL_MUTATION", "EXECUTES_COMMANDS", "DIGEST_VERIFIED"}:
            continue
        payload[key] = str(value).replace("\n", " ").strip()
    if payload["DECISION"] == "PASS" and payload["ACHIEVED"] != "T0":
        payload["EXIT_CODE"] = "0"
        payload["ELIGIBLE"] = "YES"
        payload["COMPLETION"] = "YES"
        payload["DENY_CLASS"] = "NONE"
    else:
        payload["DECISION"] = "BLOCK"
        if payload["EXIT_CODE"] not in {"2", "3"}:
            payload["EXIT_CODE"] = "2"
        payload["ELIGIBLE"] = "NO"
        payload["COMPLETION"] = "NO"
    payload["AUTOMATION_ELIGIBLE"] = "YES" if payload["DECISION"] == "PASS" and payload["ACHIEVED"] == "T5" else "NO"
    payload["EXTERNAL_MUTATION"] = "NO"
    payload["EXECUTES_COMMANDS"] = "NO"
    payload["DIGEST_VERIFIED"] = "NO"
    return payload


def format_report(fields: dict[str, str]) -> str:
    return "".join(f"{key}={fields[key]}\n" for key in REPORT_KEYS if key in fields)


def input_report(exc: TrustInputError, expected: dict[str, Any] | None, receipt: Any) -> dict[str, str]:
    head = ""
    revision = ""
    feature = ""
    repo = ""
    workstream = ""
    if isinstance(expected, dict):
        if isinstance(expected.get("subject_head"), str):
            head = expected["subject_head"]
        if isinstance(expected.get("target_repo"), str):
            repo = expected["target_repo"]
        if isinstance(expected.get("workstream"), str):
            workstream = expected["workstream"]
    if isinstance(receipt, dict):
        if isinstance(receipt.get("subject_head"), str) and SHA_RE.fullmatch(receipt["subject_head"]):
            head = head or receipt["subject_head"]
        if isinstance(receipt.get("intent_revision"), int) and not isinstance(receipt.get("intent_revision"), bool):
            revision = str(receipt["intent_revision"])
        if isinstance(receipt.get("feature_id"), str):
            feature = receipt["feature_id"]
    return blank_report(
        EXIT_CODE="3",
        REASON=exc.reason,
        DENY_CLASS=exc.deny_class,
        SUBJECT_HEAD=head,
        INTENT_REVISION=revision,
        FEATURE=feature,
        TARGET_REPO=repo,
        WORKSTREAM=workstream,
    )


def require_expected(expected: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(expected, dict):
        raise TrustInputError("expected binding must be an object", "MALFORMED")
    reject_untrusted(expected, "expected")
    repo = expected.get("target_repo")
    workstream = expected.get("workstream")
    revision = expected.get("intent_revision")
    head = expected.get("subject_head")
    if not isinstance(repo, str) or REPO_RE.fullmatch(repo) is None:
        raise TrustInputError("expected.target_repo is invalid", "MALFORMED")
    if not isinstance(workstream, str) or WORKSTREAM_RE.fullmatch(workstream) is None:
        raise TrustInputError("expected.workstream is invalid", "MALFORMED")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1 or revision > 1000000:
        raise TrustInputError("expected.intent_revision must be an integer >= 1", "MALFORMED")
    if not isinstance(head, str) or SHA_RE.fullmatch(head) is None:
        raise TrustInputError("expected.subject_head must be a lowercase 40-hex SHA", "MALFORMED")
    return {
        "target_repo": repo,
        "workstream": workstream,
        "intent_revision": revision,
        "subject_head": head,
    }


def classify(pairs: list[tuple[str, str]], index: dict[tuple[str, str], str]) -> str:
    unknown = failed = missing = False
    for pair in pairs:
        status = index.get(pair)
        if status is None:
            missing = True
        elif status == "UNKNOWN":
            unknown = True
        elif status != "PASS":
            failed = True
    if unknown:
        return "UNKNOWN"
    if failed:
        return "FAIL"
    if missing:
        return "MISSING"
    return "PASS"


def independent_verdict(boundary: dict[str, Any], subject_head: str) -> tuple[str, str]:
    block = boundary["verifier"]
    request = {
        "subject_head": subject_head,
        "change_risk": block["change_risk"],
        "implementer": {
            "identity": block["implementer"]["identity"],
            "context_id": block["implementer"]["context_id"],
        },
        "verifier": {
            "identity": block["verifier"]["identity"],
            "context_id": block["verifier"]["context_id"],
        },
        "oracle_evidence": [
            {"id": block["oracle_id"], "result": block["oracle_result"], "subject_head": subject_head}
        ],
        "review_findings": [],
        "mutable_evidence": [
            {
                "kind": "ci",
                "subject_id": block["ci_subject_id"],
                "version_id": subject_head,
                "result": block["ci_result"],
            }
        ],
        "expected_mutable": {"ci": {"subject_id": block["ci_subject_id"], "version_id": subject_head}},
    }
    module = verifier_module()
    try:
        report = module.evaluate(request)
    except module.VerifierFactsError as exc:
        raise TrustInputError(exc.reason, exc.deny_class) from exc
    if not isinstance(report, dict):
        raise TrustInputError("verifier result is unreadable", "MALFORMED")
    if report.get("SUBJECT_HEAD") != subject_head:
        return "STALE_HEAD", "verifier subject does not match the boundary"
    if report.get("DECISION") == "PASS" and report.get("VERIFIER_REQUIRED") == "YES":
        return "PASS", str(report.get("REASON") or "verifier pass")
    if report.get("DECISION") != "PASS":
        return str(report.get("DENY_CLASS") or "VERIFIER_DENY"), str(report.get("REASON") or "verifier denied")
    return "VERIFIER_REQUIRED", "T4 requires independent verifier PASS with VERIFIER_REQUIRED=YES"


def bound_fields(expected: dict[str, Any], receipt: dict[str, Any], required: str) -> dict[str, str]:
    return {
        "SUBJECT_HEAD": expected["subject_head"],
        "INTENT_REVISION": str(expected["intent_revision"]),
        "FEATURE": str(receipt.get("feature_id") or ""),
        "TARGET_REPO": expected["target_repo"],
        "WORKSTREAM": expected["workstream"],
        "REQUIRED": required,
    }


def unknown_item(feature: dict[str, Any], item: dict[str, Any], declared: set[tuple[str, str]]) -> bool:
    authority = item["authority"]
    item_id = item["id"]
    if authority in {"test", "runtime", "skill", "profile"}:
        return (authority, item_id) not in declared
    if authority == "ci":
        return item_id != CI_ID
    if authority == "automation_policy":
        return item_id != feature["id"]
    if authority == "self_report":
        return item_id not in {ref_id for _, ref_id in declared}
    return True


def _assess(
    root: Path,
    receipt: Any,
    expected_raw: dict[str, Any],
    manifest: Any,
    boundary: Any = None,
) -> dict[str, str]:
    expected = require_expected(expected_raw)
    reject_untrusted(receipt, "receipt")
    malformed = schema_errors(load_schema(root, RECEIPT_SCHEMA_REL), receipt)
    if malformed:
        raise TrustInputError(malformed[0], "MALFORMED")
    if not isinstance(receipt, dict) or not isinstance(manifest, dict):
        raise TrustInputError("receipt and manifest must be objects", "MALFORMED")
    fields = bound_fields(expected, receipt, "T1")
    if receipt["target_repo"] != expected["target_repo"] or receipt["workstream"] != expected["workstream"]:
        return blank_report(REASON="receipt repository or workstream does not match the subject", DENY_CLASS="STALE_CONTEXT", **fields)
    if receipt["intent_revision"] != expected["intent_revision"]:
        return blank_report(REASON="receipt intent revision does not match the subject", DENY_CLASS="STALE_INTENT", **fields)
    if receipt["subject_head"] != expected["subject_head"]:
        return blank_report(REASON="receipt HEAD does not match the subject", DENY_CLASS="STALE_HEAD", **fields)
    map_problems = problems(root, manifest)
    if map_problems:
        code = map_problems[0].split(" ", 1)[0]
        exit_code = "3" if code in {"EXECUTION_FORBIDDEN", "UNBOUNDED_EVIDENCE", "MALFORMED"} else "2"
        return blank_report(EXIT_CODE=exit_code, REASON=map_problems[0], DENY_CLASS=code, **fields)
    feature = next((item for item in manifest["features"] if item["id"] == receipt["feature_id"]), None)
    if feature is None:
        return blank_report(REASON="feature is not in the verification map", DENY_CLASS="UNKNOWN_FEATURE", **fields)
    required = feature["minimum_level"]
    fields = bound_fields(expected, receipt, required)
    if receipt["oracle"] != feature["oracle"]:
        return blank_report(REASON="receipt oracle does not match the feature", DENY_CLASS="ORACLE_MISMATCH", **fields)
    groups, _duplicates = declared_refs(feature)
    declared = {(authority, ref_id) for authority, ids in groups.items() for ref_id in ids}
    claimed_authority = False
    self_report = False
    seen_claims: set[tuple[str, str]] = set()
    for item in receipt["items"]:
        if item["subject_head"] != receipt["subject_head"]:
            return blank_report(REASON="evidence HEAD does not match the receipt", DENY_CLASS="STALE_HEAD", **fields)
        if item["intent_revision"] != receipt["intent_revision"]:
            return blank_report(REASON="evidence intent revision does not match the receipt", DENY_CLASS="STALE_INTENT", **fields)
        if unknown_item(feature, item, declared):
            return blank_report(REASON="evidence id is not a declared reference", DENY_CLASS="UNKNOWN_REFERENCE", **fields)
        key = (item["authority"], item["id"])
        if key in seen_claims:
            return blank_report(REASON="duplicate evidence item", DENY_CLASS="DUPLICATE_EVIDENCE", **fields)
        seen_claims.add(key)
        if item["status"] == "PASS" and item["authority"] != "self_report":
            claimed_authority = True
        if item["authority"] == "self_report" and item["status"] == "PASS":
            self_report = True
    if type(boundary) is not TrustedCoordinatorBoundary:
        if boundary is None:
            if claimed_authority:
                deny, reason = "UNTRUSTED_RECEIPT", "receipt status cannot mint trust evidence"
            elif self_report:
                deny, reason = "SELF_REPORT_ONLY", "self-report cannot satisfy completion evidence"
            else:
                deny, reason = "MISSING_EVIDENCE", "trusted evidence boundary is absent"
        else:
            deny, reason = "UNTRUSTED_BOUNDARY", "raw boundary input is not a trusted coordinator precondition"
        return blank_report(ACHIEVED="T0", REASON=reason, DENY_CLASS=deny, **fields)
    boundary = boundary.payload()
    if boundary is receipt:
        return blank_report(ACHIEVED="T0", REASON="receipt cannot be the evidence boundary", DENY_CLASS="UNTRUSTED_RECEIPT", **fields)
    reject_untrusted(boundary, "boundary")
    malformed_boundary = schema_errors(load_schema(root, BOUNDARY_SCHEMA_REL), boundary)
    if malformed_boundary:
        raise TrustInputError(malformed_boundary[0], "MALFORMED")
    if (
        boundary["target_repo"] != expected["target_repo"]
        or boundary["workstream"] != expected["workstream"]
    ):
        return blank_report(REASON="boundary repository or workstream does not match the subject", DENY_CLASS="STALE_CONTEXT", **fields)
    if boundary["intent_revision"] != expected["intent_revision"]:
        return blank_report(REASON="boundary intent revision does not match the subject", DENY_CLASS="STALE_INTENT", **fields)
    if boundary["subject_head"] != expected["subject_head"]:
        return blank_report(REASON="boundary HEAD does not match the subject", DENY_CLASS="STALE_HEAD", **fields)
    index: dict[tuple[str, str], str] = {}
    for item in boundary["evidence"]:
        if item["subject_head"] != boundary["subject_head"]:
            return blank_report(REASON="boundary evidence HEAD does not match the subject", DENY_CLASS="STALE_HEAD", **fields)
        if item["intent_revision"] != boundary["intent_revision"]:
            return blank_report(REASON="boundary evidence intent revision does not match the subject", DENY_CLASS="STALE_INTENT", **fields)
        authority = item["authority"]
        if authority == "ci":
            if item["id"] != CI_ID:
                return blank_report(REASON="boundary CI id is not the exact-head reference", DENY_CLASS="UNKNOWN_REFERENCE", **fields)
        elif (authority, item["id"]) not in declared:
            return blank_report(REASON="boundary evidence id is not a declared reference", DENY_CLASS="UNKNOWN_REFERENCE", **fields)
        key = (authority, item["id"])
        if key in index:
            return blank_report(REASON="duplicate boundary evidence", DENY_CLASS="DUPLICATE_EVIDENCE", **fields)
        index[key] = item["status"]
    t1 = classify(
        [("test", item) for item in groups["test"]]
        + [("skill", item) for item in groups["skill"]]
        + [("profile", item) for item in groups["profile"]],
        index,
    )
    ci_pairs = [key for key in index if key[0] == "ci"]
    t2 = "MISSING" if not ci_pairs else classify(ci_pairs, index)
    runtime_pairs = [("runtime", item) for item in groups["runtime"]]
    t3 = "MISSING" if not runtime_pairs or not isinstance(boundary.get("runtime_subject"), str) else classify(runtime_pairs, index)
    verdict, verdict_reason = "MISSING", "independent verification evidence is missing"
    if t1 == "PASS" and t2 == "PASS" and t3 == "PASS" and isinstance(boundary.get("verifier"), dict):
        verdict, verdict_reason = independent_verdict(boundary, expected["subject_head"])
    policy_block = boundary.get("automation_policy")
    policy_ok = (
        isinstance(policy_block, dict)
        and policy_block.get("feature_id") == feature["id"]
        and policy_block.get("eligible") is True
    )
    on_allowlist = feature["id"] in manifest["automation_eligible"]
    achieved = "T0"
    blocker = ("MISSING_EVIDENCE", "required evidence is missing")
    if t1 == "PASS":
        achieved = "T1"
        if t2 == "PASS":
            achieved = "T2"
            if t3 == "PASS":
                achieved = "T3"
                if verdict == "PASS":
                    achieved = "T4"
                    if on_allowlist and policy_ok:
                        achieved = "T5"
                    elif not on_allowlist or not policy_ok:
                        blocker = ("POLICY_INELIGIBLE", "T5 requires coordinator policy eligibility outside the receipt")
                else:
                    blocker = (verdict, verdict_reason)
            elif t3 == "UNKNOWN":
                blocker = ("UNKNOWN_EVIDENCE", "runtime evidence is UNKNOWN")
            elif t3 == "FAIL":
                blocker = ("GATE_NOT_PASS", "runtime evidence is not PASS")
            else:
                blocker = ("MISSING_RUNTIME", "real runtime evidence is missing")
        elif t2 == "UNKNOWN":
            blocker = ("UNKNOWN_EVIDENCE", "CI evidence is UNKNOWN")
        elif t2 == "FAIL":
            blocker = ("GATE_NOT_PASS", "CI evidence is not PASS")
        else:
            blocker = ("MISSING_EVIDENCE", "exact-HEAD CI evidence is missing")
    elif t1 == "UNKNOWN":
        blocker = ("UNKNOWN_EVIDENCE", "deterministic evidence is UNKNOWN")
    elif t1 == "FAIL":
        blocker = ("GATE_NOT_PASS", "deterministic evidence is not PASS")
    elif self_report and not claimed_authority:
        blocker = ("SELF_REPORT_ONLY", "self-report cannot satisfy completion evidence")
    rank = {level: position for position, level in enumerate(LEVELS)}
    if rank[achieved] >= rank[required] and achieved != "T0":
        return blank_report(DECISION="PASS", ACHIEVED=achieved, REASON="evidence satisfies the feature trust level", DENY_CLASS="NONE", **fields)
    return blank_report(ACHIEVED=achieved, REASON=blocker[1], DENY_CLASS=blocker[0], **fields)


def assess(
    root: Path,
    receipt: Any,
    expected: dict[str, Any],
    manifest: Any,
    boundary: Any = None,
) -> dict[str, str]:
    """Assess one receipt. ``boundary`` must be a ``TrustedCoordinatorBoundary``.

    A raw dict or parsed JSON object stays at T0. This function does not
    establish provenance, and a ``provenance`` string is not a trust anchor.
    The public CLI never passes ``boundary``. Receipt-only results stay at T0.
    Implementer-produced output is never terminal evidence.
    """
    try:
        return _assess(root, receipt, expected, manifest, boundary)
    except TrustInputError as exc:
        return input_report(exc, expected if isinstance(expected, dict) else None, receipt)


def load_manifest(root: Path) -> dict[str, Any]:
    loaded = load_yaml_mapping(root / MAP_REL)
    if loaded is None:
        raise TrustInputError("verification map is absent", "MISSING_MANIFEST")
    return loaded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Feature verification map and trust evidence assessor")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--root", default=".")
    assess_cmd = sub.add_parser("assess")
    assess_cmd.add_argument("--root", default=".")
    assess_cmd.add_argument("--receipt", required=True)
    assess_cmd.add_argument("--expect-repo", required=True)
    assess_cmd.add_argument("--expect-workstream", required=True)
    assess_cmd.add_argument("--expect-intent-revision", required=True, type=int)
    assess_cmd.add_argument("--expect-head", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    if args.command == "check":
        report = check_contract(root)
        emit_check(report)
        return 0 if report["result"] == "PASS" else 1
    try:
        receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
        manifest = load_manifest(root)
    except (OSError, json.JSONDecodeError) as exc:
        fields = input_report(TrustInputError(str(exc).splitlines()[0], "MALFORMED"), None, None)
        sys.stdout.write(format_report(fields))
        return 3
    except TrustInputError as exc:
        fields = input_report(exc, None, None)
        sys.stdout.write(format_report(fields))
        return int(fields["EXIT_CODE"])
    fields = assess(
        root,
        receipt,
        {
            "target_repo": args.expect_repo,
            "workstream": args.expect_workstream,
            "intent_revision": args.expect_intent_revision,
            "subject_head": args.expect_head,
        },
        manifest,
    )
    sys.stdout.write(format_report(fields))
    return int(fields["EXIT_CODE"])


if __name__ == "__main__":
    raise SystemExit(main())
