#!/usr/bin/env python3
"""Adversarial fixtures for the verification map and trust assessor."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "verification-contract.py"
HEAD = "a" * 40
OTHER = "b" * 40
DIGEST = "c" * 64


def load_tool():
    spec = importlib.util.spec_from_file_location("verification_contract", TOOL)
    if spec is None or spec.loader is None:
        raise SystemExit("FAIL cannot load verification-contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def empty_role() -> dict:
    return {"tests": [], "runtime": [], "skills": [], "profiles": []}


def manifest(*, level: str, automate: bool, domains: list[str] | None = None, extra_drive: list[str] | None = None) -> dict:
    drive = empty_role()
    drive["tests"] = ["ENG-STATIC-001", *(extra_drive or [])]
    observe = empty_role()
    observe["runtime"] = ["health"]
    launch = empty_role()
    if extra_drive and "ENG-STATIC-001" in (extra_drive or []):
        launch["tests"] = ["ENG-STATIC-001"]
    return {
        "version": 1,
        "automation_eligible": ["runtime-health"] if automate else [],
        "features": [
            {
                "id": "runtime-health",
                "oracle": "RUNTIME_HEALTH_OBSERVED",
                "minimum_level": level,
                "domains": domains or ["operations-contract"],
                "references": {
                    "launch": launch,
                    "drive": drive,
                    "observe": observe,
                    "cleanup": empty_role(),
                },
            }
        ],
    }


def item(authority: str, item_id: str, *, status: str = "PASS", head: str = HEAD, revision: int = 3) -> dict:
    return {
        "authority": authority,
        "id": item_id,
        "status": status,
        "subject_head": head,
        "intent_revision": revision,
        "reference": f"{authority}/{item_id}",
        "external_digest": DIGEST,
    }


def verification(*, same_context: bool = False, same_identity: bool = False) -> dict:
    return {
        "change_risk": "HIGH",
        "implementer": {"identity": "impl-1", "context_id": "ctx-impl"},
        "verifier": {
            "identity": "impl-1" if same_identity else "ver-1",
            "context_id": "ctx-impl" if same_context or same_identity else "ctx-ver",
        },
        "oracle_id": "ENG-ORACLE-001",
        "oracle_result": "PASS",
        "ci_subject_id": "pr-67",
        "ci_result": "PASS",
    }


def expected(head: str = HEAD, revision: int = 3) -> dict:
    return {
        "target_repo": "datarelay-labs/engineering-system",
        "workstream": "trust-evidence",
        "intent_revision": revision,
        "subject_head": head,
    }


def receipt(items: list[dict], *, head: str = HEAD, runtime_subject: str | None = "worktree:canonical", revision: int = 3) -> dict:
    body = {
        "schema_version": 1,
        "kind": "trust-evidence-receipt",
        "target_repo": "datarelay-labs/engineering-system",
        "workstream": "trust-evidence",
        "intent_revision": revision,
        "subject_head": head,
        "feature_id": "runtime-health",
        "oracle": "RUNTIME_HEALTH_OBSERVED",
        "items": items,
    }
    if runtime_subject is not None:
        body["runtime_subject"] = runtime_subject
    return body


def evidence(source: str, item_id: str, *, status: str = "PASS", head: str = HEAD, revision: int = 3) -> dict:
    return {
        "authority": source,
        "id": item_id,
        "status": status,
        "subject_head": head,
        "intent_revision": revision,
    }


def boundary(evidence_items: list[dict], *, verifier: dict | None = None, policy: bool = False, runtime_subject: str | None = "worktree:canonical", head: str = HEAD, revision: int = 3) -> dict:
    body = {
        "schema_version": 1,
        "kind": "trust-evidence-boundary",
        "provenance": "coordinator-boundary",
        "target_repo": "datarelay-labs/engineering-system",
        "workstream": "trust-evidence",
        "intent_revision": revision,
        "subject_head": head,
        "evidence": evidence_items,
    }
    if runtime_subject is not None:
        body["runtime_subject"] = runtime_subject
    if verifier is not None:
        body["verifier"] = verifier
    if policy:
        body["automation_policy"] = {"feature_id": "runtime-health", "eligible": True}
    return body


def record(failed: list[str], name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        failed.append(f"{name}{': ' + detail if detail else ''}")


def probe_failures(root: Path) -> list[str]:
    module = load_tool()
    failed: list[str] = []
    valid = manifest(level="T3", automate=False)
    map_ok = module.problems(root, valid)
    record(failed, "valid-map", map_ok == [], " ".join(map_ok))
    injected = json.loads(json.dumps(valid))
    injected["features"][0]["command"] = "printf injected"
    injected_problems = module.problems(root, injected)
    record(failed, "command-field", any(item.startswith("EXECUTION_FORBIDDEN") for item in injected_problems), " ".join(injected_problems))
    duplicated = manifest(level="T1", automate=False)
    duplicated["features"][0]["references"]["launch"]["tests"] = ["ENG-STATIC-001"]
    duplicate_problems = module.problems(root, duplicated)
    record(failed, "duplicate-reference", any(item.startswith("DUPLICATE_REFERENCE") for item in duplicate_problems), " ".join(duplicate_problems))
    unknown_domain = module.problems(root, manifest(level="T1", automate=False, domains=["not-a-domain"]))
    record(failed, "unknown-domain", any(item.startswith("UNRESOLVED_DOMAIN") for item in unknown_domain), " ".join(unknown_domain))

    subject = expected()
    url_body = receipt([item("test", "ENG-STATIC-001")], runtime_subject=None)
    url_body["items"][0]["reference"] = "http://evidence.invalid/log"
    url_report = module.assess(root, url_body, subject, valid)
    record(failed, "url-field", url_report["DENY_CLASS"] == "EXECUTION_FORBIDDEN" and url_report["DECISION"] != "PASS", url_report["DENY_CLASS"])
    logged = receipt([item("test", "ENG-STATIC-001")], runtime_subject=None)
    logged["log"] = "raw secret log"
    log_report = module.assess(root, logged, subject, valid)
    record(failed, "raw-log", log_report["DENY_CLASS"] == "UNBOUNDED_EVIDENCE" and log_report["EXIT_CODE"] == "3", log_report["DENY_CLASS"])
    free_form = receipt([item("test", "ENG-STATIC-001")], runtime_subject=None)
    free_form["verifier_request"] = {"command": "printf injected", "log": "secret"}
    free_report = module.assess(root, free_form, subject, valid)
    record(failed, "verifier-request", free_report["DECISION"] != "PASS" and free_report["EXIT_CODE"] == "3", free_report["DENY_CLASS"])
    empty = receipt([], runtime_subject=None)
    empty_report = module.assess(root, empty, subject, manifest(level="T1", automate=False))
    record(failed, "empty-items", empty_report["DECISION"] != "PASS" and empty_report["DENY_CLASS"] == "MALFORMED", empty_report["DENY_CLASS"])
    zero = module.assess(root, receipt([item("test", "ENG-STATIC-001")], runtime_subject=None), expected(revision=0), manifest(level="T1", automate=False))
    record(failed, "revision-zero", zero["DENY_CLASS"] == "MALFORMED" and zero["DECISION"] != "PASS", zero["REASON"])
    upper = module.assess(root, receipt([item("test", "ENG-STATIC-001")], runtime_subject=None), expected(head="A" * 40), manifest(level="T1", automate=False))
    record(failed, "uppercase-sha", upper["DENY_CLASS"] == "MALFORMED" and upper["DECISION"] != "PASS", upper["REASON"])

    stale = module.assess(root, receipt([item("test", "ENG-STATIC-001", head=OTHER)], runtime_subject=None), subject, manifest(level="T1", automate=False))
    record(failed, "stale-head", stale["DECISION"] == "BLOCK" and stale["DENY_CLASS"] == "STALE_HEAD", stale["DENY_CLASS"])
    stale_intent = module.assess(root, receipt([item("test", "ENG-STATIC-001", revision=2)], runtime_subject=None), subject, manifest(level="T1", automate=False))
    record(failed, "stale-intent", stale_intent["DENY_CLASS"] == "STALE_INTENT" and stale_intent["DECISION"] != "PASS", stale_intent["DENY_CLASS"])
    unknown_ref = module.assess(
        root,
        receipt([item("test", "ENG-STATIC-001"), item("test", "ENG-MISSING-999")], runtime_subject=None),
        subject,
        manifest(level="T1", automate=False),
    )
    record(failed, "unknown-reference", unknown_ref["DENY_CLASS"] == "UNKNOWN_REFERENCE" and unknown_ref["DECISION"] != "PASS", unknown_ref["DENY_CLASS"])
    self_report = module.assess(root, receipt([item("self_report", "health")], runtime_subject=None), subject, manifest(level="T3", automate=False))
    record(
        failed,
        "self-report",
        self_report["ACHIEVED"] == "T0" and self_report["DECISION"] != "PASS" and self_report["DENY_CLASS"] == "SELF_REPORT_ONLY",
        self_report["ACHIEVED"] + " " + self_report["DENY_CLASS"],
    )
    fabricated = module.assess(
        root,
        receipt(
            [
                item("test", "ENG-STATIC-001"),
                item("ci", "exact-head"),
                item("runtime", "health"),
                item("automation_policy", "runtime-health"),
            ]
        ),
        subject,
        manifest(level="T5", automate=True),
    )
    record(
        failed,
        "fabricated-pass",
        fabricated["ACHIEVED"] == "T0"
        and fabricated["DECISION"] != "PASS"
        and fabricated["DENY_CLASS"] == "UNTRUSTED_RECEIPT"
        and fabricated["ACHIEVED"] not in {"T3", "T4", "T5"}
        and fabricated["COMPLETION"] == "NO"
        and fabricated["ELIGIBLE"] == "NO"
        and fabricated["AUTOMATION_ELIGIBLE"] == "NO"
        and fabricated["DIGEST_VERIFIED"] == "NO"
        and fabricated["EXTERNAL_MUTATION"] == "NO",
        fabricated["ACHIEVED"] + " " + fabricated["DENY_CLASS"],
    )
    minted = receipt([item("test", "ENG-STATIC-001")], runtime_subject=None)
    minted["independent_verification"] = verification()
    minted["automation_policy"] = {"feature_id": "runtime-health", "eligible": True}
    minted_report = module.assess(root, minted, subject, manifest(level="T5", automate=True))
    record(
        failed,
        "receipt-minted-verifier",
        minted_report["DECISION"] != "PASS" and minted_report["ACHIEVED"] not in {"T3", "T4", "T5"},
        minted_report["ACHIEVED"] + " " + minted_report["DENY_CLASS"],
    )
    claim = receipt([item("self_report", "health")], runtime_subject=None)

    def trust(payload: dict):
        return module.TrustedCoordinatorBoundary(payload)

    proven = [evidence("test", "ENG-STATIC-001"), evidence("ci", "exact-head"), evidence("runtime", "health")]
    raw_boundary = boundary(proven, verifier=verification(), policy=True)
    parsed = json.loads(json.dumps(raw_boundary))
    raw_report = module.assess(root, claim, subject, manifest(level="T5", automate=True), raw_boundary)
    parsed_report = module.assess(root, claim, subject, manifest(level="T5", automate=True), parsed)
    record(
        failed,
        "raw-boundary",
        type(raw_boundary) is dict
        and type(parsed) is dict
        and not isinstance(parsed, module.TrustedCoordinatorBoundary)
        and not hasattr(module.TrustedCoordinatorBoundary, "from_json")
        and raw_report["ACHIEVED"] == "T0"
        and raw_report["DECISION"] != "PASS"
        and raw_report["COMPLETION"] == "NO"
        and raw_report["DENY_CLASS"] == "UNTRUSTED_BOUNDARY"
        and parsed_report["ACHIEVED"] == "T0"
        and parsed_report["DECISION"] != "PASS"
        and parsed_report["COMPLETION"] == "NO",
        raw_report["ACHIEVED"] + " " + raw_report["DENY_CLASS"] + " " + parsed_report["ACHIEVED"],
    )
    missing_runtime = module.assess(
        root,
        claim,
        subject,
        manifest(level="T3", automate=False),
        trust(boundary([evidence("test", "ENG-STATIC-001"), evidence("ci", "exact-head")], runtime_subject=None)),
    )
    record(
        failed,
        "missing-runtime",
        missing_runtime["ACHIEVED"] == "T2" and missing_runtime["DECISION"] == "BLOCK" and missing_runtime["DENY_CLASS"] == "MISSING_RUNTIME",
        missing_runtime["ACHIEVED"] + " " + missing_runtime["DENY_CLASS"],
    )
    unknown = module.assess(
        root,
        claim,
        subject,
        manifest(level="T1", automate=False),
        trust(boundary([evidence("test", "ENG-STATIC-001", status="UNKNOWN")], runtime_subject=None)),
    )
    record(failed, "unknown", unknown["DECISION"] == "BLOCK" and unknown["DENY_CLASS"] == "UNKNOWN_EVIDENCE", unknown["DENY_CLASS"])

    same_context = module.assess(
        root,
        claim,
        subject,
        manifest(level="T4", automate=False),
        trust(boundary(proven, verifier=verification(same_context=True))),
    )
    record(
        failed,
        "same-context",
        same_context["DECISION"] == "BLOCK" and same_context["DENY_CLASS"] == "SAME_ACTOR" and same_context["ACHIEVED"] == "T3",
        same_context["ACHIEVED"] + " " + same_context["DENY_CLASS"],
    )
    distinct = module.assess(
        root,
        claim,
        subject,
        manifest(level="T4", automate=False),
        trust(boundary(proven, verifier=verification())),
    )
    record(
        failed,
        "distinct-verifier",
        distinct["DECISION"] == "PASS"
        and distinct["ACHIEVED"] == "T4"
        and distinct["AUTOMATION_ELIGIBLE"] == "NO"
        and distinct["EXTERNAL_MUTATION"] == "NO"
        and distinct["DIGEST_VERIFIED"] == "NO",
        distinct["DECISION"] + " " + distinct["ACHIEVED"],
    )
    with_policy = module.assess(
        root,
        claim,
        subject,
        manifest(level="T5", automate=True),
        trust(boundary(proven, verifier=verification(), policy=True)),
    )
    record(
        failed,
        "t5-no-mutation",
        with_policy["DECISION"] == "PASS"
        and with_policy["ACHIEVED"] == "T5"
        and with_policy["AUTOMATION_ELIGIBLE"] == "YES"
        and with_policy["EXTERNAL_MUTATION"] == "NO"
        and with_policy["DIGEST_VERIFIED"] == "NO"
        and "AUTOMATION" not in with_policy
        and not any(key in with_policy for key in ("MERGE", "RELEASE", "DEPLOY")),
        with_policy["AUTOMATION_ELIGIBLE"] + " " + with_policy["EXTERNAL_MUTATION"],
    )
    return failed


def test_absent_map_is_not_created() -> None:
    module = load_tool()
    path = ROOT / ".engineering" / "verification.yaml"
    if path.exists():
        fail("canonical verification.yaml must stay absent")
    report = module.check_contract(ROOT)
    if report["result"] != "PASS" or report["state"] != "ABSENT":
        fail(f"absent map check returned {report}")
    completed = subprocess.run(
        [sys.executable, str(TOOL), "check", "--root", str(ROOT)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0 or "VERIFICATION_CONTRACT=ABSENT" not in completed.stdout:
        fail(completed.stdout + completed.stderr)
    if path.exists():
        fail("check created verification.yaml")


def test_helper_stays_production_only() -> None:
    text = TOOL.read_text(encoding="utf-8")
    for banned in ("subprocess", "os.system", "urlopen", "shell=True", "probe_failures", "verifier_request"):
        if banned in text:
            fail(f"verification tool contains {banned}")
    if "evaluate(" not in text or "AUTOMATION_ELIGIBLE" not in text or "EXTERNAL_MUTATION" not in text:
        fail("helper is missing T4 reuse or T5 eligibility output")
    if 'add_argument("--boundary"' in text or "args.boundary" in text:
        fail("public CLI accepts a boundary file")
    if "Implementer-produced output is never terminal evidence." not in text:
        fail("helper does not state that implementer output is never terminal evidence")
    if "skills-contract" in text or "openssl" in text or "ed25519" in text:
        fail("verification tool depends on skills-contract signature verification")
    adopt = (ROOT / "tools" / "adopt.py").read_text(encoding="utf-8")
    start = adopt.index("VERIFICATION_CONTRACT_MANAGED = (")
    block = adopt[start : adopt.index(")", start)]
    if "tools/independent_verifier.py" not in block:
        fail("verification adoption does not manage the independent verifier")
    if "verification.yaml" in block or "test_verification_contract.py" in block or "test_independent_verifier.py" in block:
        fail("adoption managed set includes the manifest or test fixtures")


def test_fail_closed_probes() -> None:
    verifier_spec = importlib.util.spec_from_file_location(
        "independent_verifier_probe",
        ROOT / "tools" / "independent_verifier.py",
    )
    if verifier_spec is None or verifier_spec.loader is None:
        fail("cannot load independent verifier")
    verifier = importlib.util.module_from_spec(verifier_spec)
    verifier_spec.loader.exec_module(verifier)
    direct = verifier.evaluate(
        {
            "subject_head": HEAD,
            "change_risk": "HIGH",
            "implementer": {"identity": "impl-1", "context_id": "ctx-impl"},
            "verifier": {"identity": "ver-1", "context_id": "ctx-impl"},
            "oracle_evidence": [{"id": "ENG-ORACLE-001", "result": "PASS", "subject_head": HEAD}],
            "review_findings": [],
            "mutable_evidence": [{"kind": "ci", "subject_id": "pr-67", "version_id": HEAD, "result": "PASS"}],
            "expected_mutable": {"ci": {"subject_id": "pr-67", "version_id": HEAD}},
        }
    )
    if direct.get("DENY_CLASS") != "SAME_ACTOR":
        fail(f"same-context fixture is not the verifier SAME_ACTOR case: {direct}")
    failed = probe_failures(ROOT)
    if failed:
        fail("probes: " + "; ".join(failed))


def report_fields(text: str) -> dict[str, str]:
    found = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        found[key] = value
    return found


def test_cli_boundary_rejected_and_receipt_is_not_terminal() -> None:
    """Caller --boundary is rejected. A caller-created boundary cannot elevate through the public CLI."""
    quality = (ROOT / "standards" / "QUALITY.md").read_text(encoding="utf-8")
    if "Implementer-produced output is never terminal evidence." not in quality:
        fail("QUALITY.md does not state that implementer-produced output is never terminal evidence")
    module = load_tool()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        engineering = root / ".engineering"
        engineering.mkdir()
        (engineering / "project.yaml").write_text("domains:\n  - operations-contract\n", encoding="utf-8")
        (engineering / "tests.yaml").write_text("scenarios:\n  - id: ENG-STATIC-001\n", encoding="utf-8")
        (engineering / "runtime.yaml").write_text("authorities:\n  health: operations.health_command\n", encoding="utf-8")
        (engineering / "verification.yaml").write_text(
            "\n".join(
                [
                    "version: 1",
                    "automation_eligible:",
                    "  - runtime-health",
                    "features:",
                    "  - id: runtime-health",
                    "    oracle: RUNTIME_HEALTH_OBSERVED",
                    "    minimum_level: T5",
                    "    domains:",
                    "      - operations-contract",
                    "    references:",
                    "      launch:",
                    "        tests: []",
                    "        runtime: []",
                    "        skills: []",
                    "        profiles: []",
                    "      drive:",
                    "        tests:",
                    "          - ENG-STATIC-001",
                    "        runtime: []",
                    "        skills: []",
                    "        profiles: []",
                    "      observe:",
                    "        tests: []",
                    "        runtime:",
                    "          - health",
                    "        skills: []",
                    "        profiles: []",
                    "      cleanup:",
                    "        tests: []",
                    "        runtime: []",
                    "        skills: []",
                    "        profiles: []",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        schemas = root / "schemas"
        schemas.mkdir()
        for name in (
            "verification-contract.schema.json",
            "trust-evidence-receipt.schema.json",
            "trust-evidence-boundary.schema.json",
        ):
            shutil.copy(ROOT / "schemas" / name, schemas / name)
        claim = receipt(
            [
                item("test", "ENG-STATIC-001"),
                item("ci", "exact-head"),
                item("runtime", "health"),
                item("automation_policy", "runtime-health"),
            ]
        )
        caller_boundary = boundary(
            [evidence("test", "ENG-STATIC-001"), evidence("ci", "exact-head"), evidence("runtime", "health")],
            verifier=verification(),
            policy=True,
        )
        receipt_path = root / "receipt.json"
        boundary_path = root / "boundary.json"
        receipt_path.write_text(json.dumps(claim), encoding="utf-8")
        boundary_path.write_text(json.dumps(caller_boundary), encoding="utf-8")
        raw_level = module.assess(root, claim, expected(), manifest(level="T5", automate=True), caller_boundary)
        if raw_level["ACHIEVED"] != "T0" or raw_level["DECISION"] == "PASS" or raw_level["COMPLETION"] != "NO":
            fail(f"raw boundary elevated: {raw_level['ACHIEVED']} {raw_level['DENY_CLASS']}")
        internal = module.assess(
            root,
            claim,
            expected(),
            manifest(level="T5", automate=True),
            module.TrustedCoordinatorBoundary(caller_boundary),
        )
        if internal["ACHIEVED"] != "T5" or internal["DECISION"] != "PASS":
            fail(f"trusted-coordinator precondition did not calculate T5: {internal['ACHIEVED']} {internal['DENY_CLASS']}")
        base = [
            sys.executable,
            str(TOOL),
            "assess",
            "--root",
            str(root),
            "--receipt",
            str(receipt_path),
            "--expect-repo",
            "datarelay-labs/engineering-system",
            "--expect-workstream",
            "trust-evidence",
            "--expect-intent-revision",
            "3",
            "--expect-head",
            HEAD,
        ]
        cli = subprocess.run(base, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        fields = report_fields(cli.stdout)
        if cli.returncode == 0 or fields.get("DECISION") == "PASS" or fields.get("ACHIEVED") != "T0":
            fail(f"CLI receipt elevated: rc={cli.returncode} {cli.stdout} {cli.stderr}")
        if fields.get("COMPLETION") != "NO" or fields.get("ELIGIBLE") != "NO" or fields.get("AUTOMATION_ELIGIBLE") != "NO":
            fail(f"CLI receipt treated as terminal evidence: {cli.stdout}")
        if fields.get("DENY_CLASS") != "UNTRUSTED_RECEIPT" or fields.get("EXTERNAL_MUTATION") != "NO":
            fail(f"CLI receipt deny mismatch: {cli.stdout}")
        rejected = subprocess.run(
            [*base, "--boundary", str(boundary_path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        combined = rejected.stdout + rejected.stderr
        if rejected.returncode == 0 or "unrecognized arguments" not in combined.lower():
            fail(f"CLI accepted --boundary: rc={rejected.returncode} {combined}")
        if "DECISION=PASS" in combined or any(f"ACHIEVED=T{level}" in combined for level in range(1, 6)):
            fail(f"rejected --boundary still elevated: {combined}")
        help_text = subprocess.run(
            [sys.executable, str(TOOL), "assess", "--help"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if help_text.returncode != 0 or "--boundary" in help_text.stdout:
            fail(help_text.stdout + help_text.stderr)


def main() -> None:
    test_absent_map_is_not_created()
    test_helper_stays_production_only()
    test_fail_closed_probes()
    test_cli_boundary_rejected_and_receipt_is_not_terminal()
    print("PASS verification contract")


if __name__ == "__main__":
    main()
