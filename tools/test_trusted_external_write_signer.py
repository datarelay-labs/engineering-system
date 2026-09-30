#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNER = ROOT / "tools/trusted_external_write_signer.py"

def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec); sys.modules[name]=m; spec.loader.exec_module(m); return m

fixtures=load("signer_fixtures", ROOT/"tools/skills_contract_fixtures.py")
skills=load("signer_skills", ROOT/"tools/skills-contract.py")
worker=load("signer_worker", ROOT/"tools/worker_adapter.py")

def run(*args):
    return subprocess.run([sys.executable,str(SIGNER),*map(str,args)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)

def base_effect():
    return {
      "branch": (subprocess.check_output(["git","branch","--show-current"],cwd=ROOT,text=True).strip() or "ci/detached-head"),
      "expected_status":"ACTIVE","intent_revision":7,
      "mutation_content":"bounded content",
      "mutation_target":{"kind":"issue","id":"999"},
      "requested_action":"update_work_packet",
      "subject_head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
      "target_repo":"datarelay-labs/engineering-system",
      "workstream":"trusted-external-write-runtime",
    }

def main():
    with tempfile.TemporaryDirectory() as td:
        t=Path(td); priv,pub=fixtures.generate_keypair(t)
        effect=t/"effect.json"; binding=t/"binding.json"; dispatch=t/"dispatch.json"
        effect.write_text(json.dumps(base_effect()),encoding="utf-8")
        cp=run("issue","--root",ROOT,"--effect-json",effect,"--binding-out",binding,"--dispatch-out",dispatch,
               "--session-id","chatgpt-test-1","--dispatch-id","dispatch-test-1",
               "--authority-permission","admin","--ttl-seconds","120","--test-mode","--private-key",priv,"--public-key",pub)
        assert cp.returncode==0,cp.stdout
        b=json.loads(binding.read_text()); d=json.loads(dispatch.read_text())
        assert b["profile"]=="external_write"
        assert b["approved_classes"]==["external_write"]
        assert b["scope"]["subject_head"]==base_effect()["subject_head"]
        assert d["tool_id"]=="network.post"
        assert d["classes"]==["external_write","network"]
        assert d["request_sha256"]==skills.canonical_request_sha256(base_effect())
        assert d["expires_at_unix"]>int(time.time())

        # Exact worker-adapter request hash must match the signer effect.
        bound={
          "branch":base_effect()["branch"],"expected_status":"ACTIVE","intent_revision":7,
          "requested_action":"update_work_packet","subject_head":base_effect()["subject_head"],
          "target_repo":"datarelay-labs/engineering-system","workstream":"trusted-external-write-runtime",
        }
        proposed={"target":{"kind":"issue","id":"999"},"content":"bounded content"}
        assert worker.concrete_effect(bound,proposed)==base_effect()

        # Absolute authority boundary: production mode cannot use caller keys.
        blocked=run("issue","--root",ROOT,"--effect-json",effect,"--binding-out",t/"b2","--dispatch-out",t/"d2",
                    "--session-id","chatgpt-test-2","--dispatch-id","dispatch-test-2","--authority-permission","admin",
                    "--private-key",priv,"--public-key",pub)
        assert blocked.returncode==3
        assert "production signer must run as root" in blocked.stdout or "production minting requires a host coordinator" in blocked.stdout

        # Unknown/arbitrary keys, bad action, origin/repo mismatch, TTL expansion fail closed.
        bad=base_effect(); bad["command"]="rm -rf /"; effect.write_text(json.dumps(bad),encoding="utf-8")
        assert run("issue","--root",ROOT,"--effect-json",effect,"--binding-out",t/"b3","--dispatch-out",t/"d3",
                   "--session-id","chatgpt-test-3","--dispatch-id","dispatch-test-3","--authority-permission","admin",
                   "--test-mode","--private-key",priv,"--public-key",pub).returncode==3
        bad=base_effect(); bad["requested_action"]="delete_repository"; effect.write_text(json.dumps(bad),encoding="utf-8")
        assert run("issue","--root",ROOT,"--effect-json",effect,"--binding-out",t/"b4","--dispatch-out",t/"d4",
                   "--session-id","chatgpt-test-4","--dispatch-id","dispatch-test-4","--authority-permission","admin",
                   "--test-mode","--private-key",priv,"--public-key",pub).returncode==3
        bad=base_effect(); bad["target_repo"]="evil/example"; effect.write_text(json.dumps(bad),encoding="utf-8")
        assert run("issue","--root",ROOT,"--effect-json",effect,"--binding-out",t/"b5","--dispatch-out",t/"d5",
                   "--session-id","chatgpt-test-5","--dispatch-id","dispatch-test-5","--authority-permission","admin",
                   "--test-mode","--private-key",priv,"--public-key",pub).returncode==3
        effect.write_text(json.dumps(base_effect()),encoding="utf-8")
        assert run("issue","--root",ROOT,"--effect-json",effect,"--binding-out",t/"b6","--dispatch-out",t/"d6",
                   "--session-id","chatgpt-test-6","--dispatch-id","dispatch-test-6","--authority-permission","admin",
                   "--ttl-seconds","301","--test-mode","--private-key",priv,"--public-key",pub).returncode==3

        # Existing output cannot be overwritten.
        assert run("issue","--root",ROOT,"--effect-json",effect,"--binding-out",binding,"--dispatch-out",t/"d7",
                   "--session-id","chatgpt-test-7","--dispatch-id","dispatch-test-7","--authority-permission","admin",
                   "--test-mode","--private-key",priv,"--public-key",pub).returncode!=0
    print("TRUSTED_EXTERNAL_WRITE_SIGNER_TESTS=PASS")

if __name__=="__main__":
    main()
