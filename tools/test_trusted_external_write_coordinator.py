#!/usr/bin/env python3
from __future__ import annotations
import base64,importlib.util,json,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TOOL=ROOT/"tools/trusted_external_write_coordinator.py"
SIGNER=ROOT/"tools/trusted_external_write_signer.py"
def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
fixtures=load("coord_fixtures",ROOT/"tools/skills_contract_fixtures.py")
coordinator=load("trusted_external_write_coordinator",TOOL)
HEAD=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
BR=subprocess.check_output(["git","branch","--show-current"],cwd=ROOT,text=True).strip() or "ci/detached-head"
def effect():
    return {"branch":BR,"expected_status":"ACTIVE","intent_revision":2,"mutation_content":"bounded content","mutation_target":{"kind":"issue","id":"777"},"requested_action":"update_work_packet","subject_head":HEAD,"target_repo":"datarelay-labs/engineering-system","workstream":"chatgpt-only-bootstrap"}
def packet(implementer="CHATGPT_CHAT"):
    return "\n".join(["PACKET_VERSION=2","TARGET_REPO=datarelay-labs/engineering-system","WORKSTREAM=chatgpt-only-bootstrap","STATUS=ACTIVE",f"BRANCH={BR}","TASK_KIND=IMPLEMENTATION","OWNER_INTENT=bootstrap","LAST_VERIFIED_HEAD="+HEAD,"INTENT_REVISION=2","CHANGE_RISK=HIGH",f"IMPLEMENTER={implementer}",""])
def fakegh(path,permission="admin",implementer="CHATGPT_CHAT",head=HEAD,include_profile=True):
    profile_bytes=(ROOT/".engineering/execution-profile.yaml").read_bytes()
    project_bytes=(ROOT/".engineering/project.yaml").read_bytes()
    responses={
      "repos/datarelay-labs/engineering-system/issues/777":{"number":777,"state":"open","title":"[AI Work] Test packet","body":packet(implementer),"user":{"login":"RickLee-kr"}},
      "repos/datarelay-labs/engineering-system/collaborators/RickLee-kr/permission":{"permission":permission},
      f"repos/datarelay-labs/engineering-system/commits/{BR}":{"sha":head},
      f"repos/datarelay-labs/engineering-system/contents/.engineering/project.yaml?ref={HEAD}":{"encoding":"base64","content":base64.b64encode(project_bytes).decode("ascii")},
      f"repos/datarelay-labs/engineering-system/contents/.engineering/execution-profile.yaml?ref={HEAD}":{"encoding":"base64","content":base64.b64encode(profile_bytes).decode("ascii")},
    }
    if not include_profile:
        responses.pop(f"repos/datarelay-labs/engineering-system/contents/.engineering/execution-profile.yaml?ref={HEAD}")
    code="#!/usr/bin/env python3\nimport json,sys\nr="+repr(responses)+"\na=sys.argv[1:]\nkey=a[1] if len(a)>1 and a[0]==\"api\" else \"\"\nif key not in r: sys.exit(2)\nprint(json.dumps(r[key]))\n"
    path.write_text(code);path.chmod(0o755)
def run(t,gh,priv,pub):
    e=t/"effect.json";e.write_text(json.dumps(effect()));b=t/"binding.json";d=t/"dispatch.json"
    cp=subprocess.run([sys.executable,str(TOOL),"authorize","--repository","datarelay-labs/engineering-system","--issue-id","777","--root",str(ROOT),"--effect-json",str(e),"--binding-out",str(b),"--dispatch-out",str(d),"--session-id","chatgpt-bootstrap-1","--dispatch-id","bootstrap-dispatch-1","--ttl-seconds","120","--test-mode","--private-key",str(priv),"--public-key",str(pub),"--test-gh",str(gh),"--test-signer",str(SIGNER)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    return cp,b,d
def test_profile_authority_is_read_from_exact_subject(base:Path):
    gh=base/"subject-gh";fakegh(gh)
    profile=coordinator._canonical_execution_profile(
        gh,"datarelay-labs/engineering-system",HEAD
    )
    assert profile["authority_contract"]=="profile-v3"

    missing=base/"missing-profile-gh";fakegh(missing,include_profile=False)
    try:
        coordinator._canonical_execution_profile(
            missing,"datarelay-labs/engineering-system",HEAD
        )
    except coordinator.CoordinatorError as exc:
        assert "execution profile is unavailable" in str(exc),str(exc)
    else:
        raise AssertionError("subject without committed profile was authorized")

def main():
    with tempfile.TemporaryDirectory() as td:
        base=Path(td);priv,pub=fixtures.generate_keypair(base)
        test_profile_authority_is_read_from_exact_subject(base)
        for name,perm,impl,head,ok,needle in [
          ("ok","admin","CHATGPT_CHAT",HEAD,True,""),
          ("permission","read","CHATGPT_CHAT",HEAD,False,"permission is insufficient"),
          ("head","admin","CHATGPT_CHAT","0"*40,False,"branch HEAD mismatch"),
          ("implementer","admin","OTHER",HEAD,False,"execution profile is not authorized"),
        ]:
            t=base/name;t.mkdir();gh=t/"gh";fakegh(gh,perm,impl,head)
            cp,b,d=run(t,gh,priv,pub)
            if ok:
                assert cp.returncode==0,cp.stdout
                assert b.is_file() and d.is_file()
            else:
                assert cp.returncode==3,cp.stdout
                assert needle in cp.stdout,cp.stdout
    print("TRUSTED_EXTERNAL_WRITE_COORDINATOR_TESTS=PASS")
if __name__=="__main__":main()
