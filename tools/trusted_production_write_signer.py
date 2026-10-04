#!/usr/bin/env python3
"""Root-only issuer for one exact shell.production_write authorization."""
from __future__ import annotations
import argparse, base64, hashlib, importlib.util, json, os, stat, subprocess, sys, tempfile, time
from pathlib import Path

OPENSSL=Path("/usr/bin/openssl")
KEY=Path("/etc/engineering-system/skills-trust-anchor.key")
PUB=Path("/etc/engineering-system/skills-trust-anchor.pub")
SKILLS=Path("/usr/lib/engineering-system/skills-contract.py")
GIT=Path("/usr/bin/git")
MAX_REQUEST=64*1024
class SignerError(Exception):pass

def _git_env():
    return {"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C","GIT_CONFIG_NOSYSTEM":"1","GIT_NO_REPLACE_OBJECTS":"1","HOME":"/nonexistent","XDG_CONFIG_HOME":"/nonexistent"}

def _git(root,*args):
    cp=subprocess.run([str(GIT),"-c",f"safe.directory={root}","-c","core.hooksPath=/dev/null","-c","core.fsmonitor=false",*args],
        cwd=root,env=_git_env(),check=False,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if cp.returncode: raise SignerError("trusted production worktree identity check failed")
    return cp.stdout.strip()

def _git_bytes(root,*args):
    cp=subprocess.run([str(GIT),"-c",f"safe.directory={root}","-c","core.hooksPath=/dev/null","-c","core.fsmonitor=false",*args],
        cwd=root,env=_git_env(),check=False,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if cp.returncode: raise SignerError("trusted production worktree identity check failed")
    return cp.stdout

def _secure_node(path,allow_symlink=False):
    try: st=path.lstat()
    except OSError as exc: raise SignerError("production worktree provenance unavailable") from exc
    if st.st_uid!=0: raise SignerError("production worktree provenance invalid")
    if stat.S_ISLNK(st.st_mode):
        if not allow_symlink: raise SignerError("production worktree provenance invalid")
    elif st.st_mode & 0o022:
        raise SignerError("production worktree provenance invalid")
    return st

def _require_root_owned_checkout(root):
    _secure_node(root)
    _secure_node(root.parent)
    gitdir=root/".git"
    st=_secure_node(gitdir)
    if not stat.S_ISDIR(st.st_mode): raise SignerError("production worktree Git metadata invalid")
    for dirpath,dirnames,filenames in os.walk(gitdir,topdown=True,followlinks=False):
        base=Path(dirpath); _secure_node(base)
        for name in dirnames:
            child=base/name
            cst=_secure_node(child)
            if not stat.S_ISDIR(cst.st_mode): raise SignerError("production worktree Git metadata invalid")
        for name in filenames:
            child=base/name
            cst=_secure_node(child)
            if not stat.S_ISREG(cst.st_mode): raise SignerError("production worktree Git metadata invalid")

def _blob_sha1(raw):
    h=hashlib.sha1()
    h.update(f"blob {len(raw)}\0".encode("ascii"));h.update(raw)
    return h.hexdigest()

def _regular_blob_sha1(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode): raise SignerError("production worktree entry type mismatch")
        h=hashlib.sha1();h.update(f"blob {st.st_size}\0".encode("ascii"));total=0
        while True:
            chunk=os.read(fd,1024*1024)
            if not chunk: break
            total+=len(chunk);h.update(chunk)
        if total!=st.st_size: raise SignerError("production worktree file changed during verification")
        return st,h.hexdigest()
    finally: os.close(fd)

def _verify_committed_tree(root,subject_head,root_provenance=True):
    raw=_git_bytes(root,"ls-tree","-rz","--full-tree",subject_head)
    expected={};expected_dirs=set()
    for record in raw.split(b"\0"):
        if not record: continue
        try:
            meta,path_raw=record.split(b"\t",1)
            mode,kind,oid=meta.decode("ascii").split()
            rel=path_raw.decode("utf-8")
        except (ValueError,UnicodeError) as exc:
            raise SignerError("production worktree tree metadata invalid") from exc
        parts=rel.split("/")
        if not rel or any(part in {"",".",".."} for part in parts) or parts[0]==".git":
            raise SignerError("production worktree tree path invalid")
        if kind!="blob" or mode not in {"100644","100755","120000"}:
            raise SignerError("production worktree tree entry unsupported")
        expected[rel]=(mode,oid)
        for i in range(1,len(parts)): expected_dirs.add("/".join(parts[:i]))
    actual=set();actual_dirs=set()
    for dirpath,dirnames,filenames in os.walk(root,topdown=True,followlinks=False):
        base=Path(dirpath)
        if base==root and ".git" in dirnames: dirnames.remove(".git")
        if root_provenance:
            bst=base.lstat()
            if bst.st_uid!=0 or bst.st_mode & 0o022: raise SignerError("production worktree provenance invalid")
        relbase="" if base==root else base.relative_to(root).as_posix()
        for name in list(dirnames):
            path=base/name;st=path.lstat();rel=f"{relbase}/{name}" if relbase else name
            if root_provenance and (st.st_uid!=0 or (not stat.S_ISLNK(st.st_mode) and st.st_mode & 0o022)):
                raise SignerError("production worktree provenance invalid")
            if stat.S_ISLNK(st.st_mode):
                actual.add(rel);dirnames.remove(name)
            elif stat.S_ISDIR(st.st_mode):
                actual_dirs.add(rel)
            else:
                raise SignerError("production worktree entry type mismatch")
        for name in filenames:
            path=base/name;st=path.lstat();rel=f"{relbase}/{name}" if relbase else name
            if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                raise SignerError("production worktree entry type mismatch")
            if root_provenance and (st.st_uid!=0 or (not stat.S_ISLNK(st.st_mode) and st.st_mode & 0o022)):
                raise SignerError("production worktree provenance invalid")
            actual.add(rel)
    if actual!=set(expected) or actual_dirs!=expected_dirs:
        raise SignerError("production worktree content set mismatch")
    for rel,(mode,oid) in expected.items():
        path=root/rel;st=path.lstat()
        if root_provenance and st.st_uid!=0: raise SignerError("production worktree provenance invalid")
        if mode=="120000":
            if not stat.S_ISLNK(st.st_mode): raise SignerError("production worktree entry type mismatch")
            content=os.fsencode(os.readlink(path))
        else:
            if root_provenance and st.st_mode & 0o022: raise SignerError("production worktree provenance invalid")
            st,digest=_regular_blob_sha1(path)
            if root_provenance and (st.st_uid!=0 or st.st_mode & 0o022): raise SignerError("production worktree provenance invalid")
            executable=bool(st.st_mode & 0o111)
            if executable!=(mode=="100755"): raise SignerError("production worktree executable mode mismatch")
            if digest!=oid: raise SignerError("production worktree content differs from approved HEAD")
            continue
        if _blob_sha1(content)!=oid: raise SignerError("production worktree content differs from approved HEAD")

def _verify_local_scope(a,root_provenance=True):
    root=a.root
    if not root.is_dir() or root.is_symlink(): raise SignerError("production worktree must be a real directory")
    if root_provenance: _require_root_owned_checkout(root)
    resolved=root.resolve()
    top=Path(_git(root,"rev-parse","--show-toplevel")).resolve()
    if top!=resolved: raise SignerError("production worktree root mismatch")
    if _git(root,"rev-parse","--show-object-format")!="sha1": raise SignerError("production worktree object format unsupported")
    if _git(root,"rev-parse","HEAD").lower()!=str(a.subject_head).lower(): raise SignerError("production worktree subject_head mismatch")
    branch=_git(root,"branch","--show-current")
    if branch!=a.branch: raise SignerError("production worktree branch mismatch")
    suffix=a.repository
    accepted={f"https://github.com/{suffix}",f"https://github.com/{suffix}.git",f"git@github.com:{suffix}",f"git@github.com:{suffix}.git",f"ssh://git@github.com/{suffix}",f"ssh://git@github.com/{suffix}.git"}
    if _git(root,"remote","get-url","origin") not in accepted: raise SignerError("production worktree origin repository mismatch")
    _verify_committed_tree(root,a.subject_head,root_provenance=root_provenance)

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
        _verify_local_scope(a)
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
