#!/usr/bin/env python3
"""Root-only issuer for one exact shell.production_write authorization."""
from __future__ import annotations
import argparse, base64, hashlib, importlib.util, json, os, subprocess, sys, tempfile, time
from pathlib import Path

OPENSSL=Path("/usr/bin/openssl")
KEY=Path("/etc/engineering-system/skills-trust-anchor.key")
PUB=Path("/etc/engineering-system/skills-trust-anchor.pub")
SKILLS=Path("/usr/lib/engineering-system/skills-contract.py")
MAX_REQUEST=64*1024
class SignerError(Exception):pass

def _load():
    spec=importlib.util.spec_from_file_location("trusted_prod_skills",SKILLS)
    if spec is None or spec.loader is None: raise SignerError("skills contract unavailable")
    m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m); return m
def _sign(payload):
    raw=json.dumps({k:payload[k] for k in sorted(payload)},sort_keys=True,separators=(",",":")).encode()
    with tempfile.TemporaryDirectory() as d:
        msg=Path(d)/"m"; sig=Path(d)/"s"; msg.write_bytes(raw)
        cp=subprocess.run([str(OPENSSL),"pkeyutl","-sign","-inkey",str(KEY),"-rawin","-in",str(msg),"-out",str(sig)],
            env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C"},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if cp.returncode: raise SignerError("signing failed")
        return {**payload,"signature":base64.b64encode(sig.read_bytes()).decode()}
def _write(path,payload):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try: os.write(fd,(json.dumps(payload,sort_keys=True,indent=2)+"\n").encode()); os.fsync(fd)
    finally: os.close(fd)
def issue(a):
    if os.geteuid()!=0: raise SignerError("root is required")
    if not 1 <= a.ttl_seconds <= 300: raise SignerError("ttl_seconds must be 1..300")
    try:
        key_stat = os.stat(KEY, follow_symlinks=False)
        pub_stat = os.stat(PUB, follow_symlinks=False)
    except OSError as exc:
        raise SignerError("trust anchor unavailable") from exc
    if key_stat is None or not KEY.is_file() or KEY.is_symlink() or key_stat.st_uid != 0 or (key_stat.st_mode & 0o077) != 0:
        raise SignerError("private trust anchor provenance invalid")
    if pub_stat is None or not PUB.is_file() or PUB.is_symlink() or pub_stat.st_uid != 0 or (pub_stat.st_mode & 0o022) != 0:
        raise SignerError("public trust anchor provenance invalid")
    authority_basis=getattr(a,"authority_basis","collaborator_permission")
    if authority_basis == "collaborator_permission":
        if a.authority_permission not in {"write","maintain","admin"}: raise SignerError("trusted authority permission required")
    elif authority_basis == "production_approver_policy":
        if a.authority_permission != "production_approver": raise SignerError("trusted production approver authority required")
    else:
        raise SignerError("trusted authority basis required")
    raw=a.request_json.read_bytes()
    if len(raw)>MAX_REQUEST: raise SignerError("request too large")
    try:req=json.loads(raw)
    except json.JSONDecodeError as e: raise SignerError("request JSON invalid") from e
    if not isinstance(req,dict): raise SignerError("request must be object")
    skills=_load(); _,_,digest=skills.load_effective_state(a.root)
    ph=hashlib.sha256(PUB.read_bytes()).hexdigest()
    scope={"target_repo":a.repository,"worktree":str(a.root.resolve()),"workstream":a.workstream,
           "branch":a.branch,"subject_head":a.subject_head,"intent_revision":a.intent_revision,"session_id":a.session_id}
    binding=_sign({"profile":"production_write","policy_digest":digest,"authority_permission":a.authority_permission,
      "authority_basis":authority_basis,"approved_classes":["destructive","production_write"],"public_key_sha256":ph,"scope":scope})
    now=int(time.time())
    dispatch=_sign({"tool_id":"shell.production_write","classes":sorted(skills.DEFAULT_TOOL_REGISTRY["shell.production_write"]),
      "policy_digest":digest,"binding_public_key_sha256":ph,"binding_sha256":skills.signed_payload_sha256(binding),
      "worktree":str(a.root.resolve()),"request_sha256":skills.canonical_request_sha256(req),"session_id":a.session_id,
      "scope_sha256":skills.canonical_request_sha256(scope),"dispatch_id":a.dispatch_id,"expires_at_unix":now+a.ttl_seconds})
    _write(a.binding_out,binding);_write(a.dispatch_out,dispatch)
    print("TRUSTED_PRODUCTION_WRITE_SIGNER=PASS"); print(f"REQUEST_SHA256={skills.canonical_request_sha256(req)}"); return 0
def parser():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--root",type=Path,required=True);p.add_argument("--request-json",type=Path,required=True)
    p.add_argument("--repository",required=True);p.add_argument("--workstream",required=True);p.add_argument("--branch",required=True);p.add_argument("--subject-head",required=True)
    p.add_argument("--intent-revision",type=int,required=True);p.add_argument("--session-id",required=True);p.add_argument("--dispatch-id",required=True)
    p.add_argument("--authority-permission",required=True);p.add_argument("--authority-basis",choices=("collaborator_permission","production_approver_policy"),default="collaborator_permission");p.add_argument("--ttl-seconds",type=int,default=120);p.add_argument("--binding-out",type=Path,required=True);p.add_argument("--dispatch-out",type=Path,required=True);return p
def main():
    try:return issue(parser().parse_args())
    except (SignerError,OSError) as e: print(f"TRUSTED_PRODUCTION_WRITE_SIGNER=BLOCK reason={e}");return 3
if __name__=="__main__":raise SystemExit(main())
