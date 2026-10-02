#!/usr/bin/env python3
"""Host-side authenticated coordinator for one exact external-write assertion mint.

Reads the canonical Work Packet and collaborator permission with a fixed trusted
GitHub CLI, compares them with one exact worker-adapter effect, then invokes the
fixed root signer. It never performs the GitHub mutation itself.
"""
from __future__ import annotations
import argparse, base64, json, os, pwd, re, stat, subprocess, sys, tempfile
from pathlib import Path
from typing import Any

import yaml

from execution_profile import (
    ProfileError,
    load_profile_text,
    packet_authority,
    parse_packet_metadata,
)

TRUSTED_GH=Path("/usr/bin/gh")
TRUSTED_SIGNER=Path("/usr/lib/engineering-system/trusted-external-write-signer")
CANONICAL_REPOSITORY="datarelay-labs/engineering-system"
EXECUTION_PROFILE_PATH=".engineering/execution-profile.yaml"
REPO_RE=re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ISSUE_RE=re.compile(r"^[0-9]+$")
SHA_RE=re.compile(r"^[0-9a-f]{40}$")
_TEST_GH: Path|None=None
_TEST_SIGNER: Path|None=None

class CoordinatorError(Exception): pass

def _trusted_exec(path: Path)->bool:
    try:
        st=path.lstat(); parent=path.parent.lstat()
        return stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode) and st.st_uid==0 and not(st.st_mode&0o022) and os.access(path,os.X_OK) and parent.st_uid==0 and not(parent.st_mode&0o022)
    except OSError:return False

def _exec_path(prod:Path,test:Path|None)->Path:
    if test is not None:return test
    if not _trusted_exec(prod):raise CoordinatorError(f"trusted executable unavailable: {prod}")
    return prod

def _github_config_home()->Path:
    if os.geteuid() != 0:
        return Path.home()
    sudo_uid = os.environ.get("SUDO_UID", "")
    if not sudo_uid.isdigit():
        raise CoordinatorError("root coordinator requires SUDO_UID to bind GitHub auth to the invoking user")
    try:
        entry = pwd.getpwuid(int(sudo_uid))
    except (KeyError, ValueError) as exc:
        raise CoordinatorError("invoking user identity is unavailable") from exc
    home = Path(entry.pw_dir)
    if not home.is_absolute() or home == Path("/root"):
        raise CoordinatorError("invoking user home is invalid")
    return home

def _run_json(gh:Path,args:list[str])->Any:
    home = _github_config_home()
    cp=subprocess.run([str(gh),*args],env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C","GH_PROMPT_DISABLED":"1","HOME":str(home),"XDG_CONFIG_HOME":str(home/".config")},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if cp.returncode:raise CoordinatorError("authenticated GitHub read failed")
    try:return json.loads(cp.stdout)
    except json.JSONDecodeError as e:raise CoordinatorError("authenticated GitHub read returned invalid JSON") from e

def _fields(body:str)->dict[str,str]:
    try:
        return parse_packet_metadata(body)
    except ProfileError as exc:
        raise CoordinatorError(str(exc)) from exc

def _effect(path:Path)->dict[str,Any]:
    try:x=json.loads(path.read_text())
    except Exception as e:raise CoordinatorError("effect JSON invalid") from e
    if not isinstance(x,dict):raise CoordinatorError("effect must be object")
    return x

def _github_blob(gh:Path,repository:str,path:str,ref:str,label:str)->bytes:
    if SHA_RE.fullmatch(ref) is None:
        raise CoordinatorError(f"{label} ref is invalid")
    try:
        doc=_run_json(gh,["api",f"repos/{repository}/contents/{path}?ref={ref}"])
    except CoordinatorError as exc:
        raise CoordinatorError(f"{label} is unavailable") from exc
    if not isinstance(doc,dict) or doc.get("encoding")!="base64" or not isinstance(doc.get("content"),str):
        raise CoordinatorError(f"{label} is unavailable")
    try:
        return base64.b64decode(doc["content"],validate=False)
    except ValueError as exc:
        raise CoordinatorError(f"{label} is invalid") from exc

def _canonical_execution_profile(gh:Path,repository:str,subject_head:str)->dict[str,Any]:
    try:
        project_bytes=_github_blob(
            gh,repository,".engineering/project.yaml",subject_head,
            "target Engineering System profile",
        )
        project=yaml.safe_load(project_bytes.decode("utf-8")) or {}
    except (UnicodeError,yaml.YAMLError) as exc:
        raise CoordinatorError("target Engineering System profile is unavailable") from exc
    engineering=project.get("engineering_system") if isinstance(project,dict) else None
    if not isinstance(engineering,dict):
        raise CoordinatorError("target Engineering System profile is invalid")
    mode=str(engineering.get("mode") or "")
    if mode=="adopted":
        baseline=str(engineering.get("baseline") or "").lower()
        if SHA_RE.fullmatch(baseline) is None:
            raise CoordinatorError("target Engineering System baseline is invalid")
    elif mode=="canonical" and repository==CANONICAL_REPOSITORY:
        baseline=subject_head
    else:
        raise CoordinatorError("target Engineering System mode is unsupported")
    canonical_bytes=_github_blob(
        gh,CANONICAL_REPOSITORY,EXECUTION_PROFILE_PATH,baseline,
        "canonical execution profile",
    )
    target_bytes=_github_blob(
        gh,repository,EXECUTION_PROFILE_PATH,subject_head,
        "target execution profile",
    )
    try:
        canonical_text=canonical_bytes.decode("utf-8")
        profile=load_profile_text(canonical_text)
    except (UnicodeError,ProfileError) as exc:
        raise CoordinatorError("canonical execution profile is invalid") from exc
    if target_bytes!=canonical_bytes:
        raise CoordinatorError("target execution profile differs from immutable canonical baseline")
    return profile


def authorize(args:argparse.Namespace)->int:
    repo=args.repository
    if REPO_RE.fullmatch(repo) is None or ISSUE_RE.fullmatch(args.issue_id) is None:raise CoordinatorError("invalid repository/issue identity")
    gh=_exec_path(TRUSTED_GH,_TEST_GH); signer=_exec_path(TRUSTED_SIGNER,_TEST_SIGNER)
    issue=_run_json(gh,["api",f"repos/{repo}/issues/{args.issue_id}"])
    if not isinstance(issue,dict) or str(issue.get("number"))!=args.issue_id:raise CoordinatorError("issue identity mismatch")
    if str(issue.get("state") or "").lower()!="open":raise CoordinatorError("Work Packet is not open")
    if not str(issue.get("title") or "").startswith("[AI Work]"):raise CoordinatorError("Issue is not an AI Work Packet")
    body=issue.get("body"); user=issue.get("user")
    if not isinstance(body,str) or not isinstance(user,dict):raise CoordinatorError("Work Packet body/author missing")
    login=str(user.get("login") or "")
    permission_doc=_run_json(gh,["api",f"repos/{repo}/collaborators/{login}/permission"])
    permission=str(permission_doc.get("permission") or "").lower() if isinstance(permission_doc,dict) else ""
    if permission not in {"write","maintain","admin"}:raise CoordinatorError("Work Packet author permission is insufficient")
    f=_fields(body); effect=_effect(args.effect_json)
    if effect.get("expected_status") != "ACTIVE":raise CoordinatorError("external write requires ACTIVE status")
    required={"TARGET_REPO":repo,"WORKSTREAM":str(effect.get("workstream") or ""),"STATUS":"ACTIVE","BRANCH":str(effect.get("branch") or ""),"INTENT_REVISION":str(effect.get("intent_revision") or "")}
    for k,v in required.items():
        if f.get(k)!=v:raise CoordinatorError(f"authoritative Work Packet mismatch: {k}")
    subject_head=str(effect.get("subject_head") or "").lower()
    if SHA_RE.fullmatch(subject_head) is None:
        raise CoordinatorError("external write subject HEAD is invalid")
    if f.get("LAST_VERIFIED_HEAD") != subject_head:raise CoordinatorError("authoritative Work Packet mismatch: LAST_VERIFIED_HEAD")
    if f.get("CHANGE_RISK") not in {"LOW","MEDIUM","HIGH"}:raise CoordinatorError("Work Packet CHANGE_RISK is missing or invalid")
    branch_doc=_run_json(gh,["api",f"repos/{repo}/commits/{effect.get('branch','')}"])
    head=str(branch_doc.get("sha") or "").lower() if isinstance(branch_doc,dict) else ""
    if SHA_RE.fullmatch(head) is None or head!=subject_head:raise CoordinatorError("authoritative branch HEAD mismatch")
    execution_profile=_canonical_execution_profile(gh,repo,subject_head)
    profile_blocking,_=packet_authority(execution_profile,f)
    if profile_blocking:raise CoordinatorError("Work Packet execution profile is not authorized: "+profile_blocking[0])
    snapshot_dir=Path(tempfile.mkdtemp(prefix="engineering-effect-",dir="/tmp"))
    snapshot=snapshot_dir/"effect.json"
    fd=os.open(snapshot,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        os.write(fd,(json.dumps(effect,sort_keys=True,separators=(",",":"))+"\n").encode("utf-8"));os.fsync(fd)
    finally:os.close(fd)
    cmd=[str(signer),"issue","--root",str(args.root),"--effect-json",str(snapshot),"--binding-out",str(args.binding_out),"--dispatch-out",str(args.dispatch_out),"--session-id",args.session_id,"--dispatch-id",args.dispatch_id,"--authority-permission",permission,"--ttl-seconds",str(args.ttl_seconds)]
    if args.test_mode:
        cmd+=["--test-mode","--private-key",str(args.private_key),"--public-key",str(args.public_key)]
    try:
        cp=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    finally:
        try:snapshot.unlink()
        except OSError:pass
        try:snapshot_dir.rmdir()
        except OSError:pass
    if cp.returncode:raise CoordinatorError("trusted signer denied assertion issuance: "+cp.stdout.strip())
    print("TRUSTED_EXTERNAL_WRITE_COORDINATOR=PASS")
    print(f"AUTHOR={login}")
    print(f"AUTHOR_PERMISSION={permission}")
    print(f"SUBJECT_HEAD={head}")
    return 0

def parser():
    p=argparse.ArgumentParser(description=__doc__); s=p.add_subparsers(dest="cmd",required=True); a=s.add_parser("authorize")
    a.add_argument("--repository",required=True);a.add_argument("--issue-id",required=True);a.add_argument("--root",type=Path,required=True);a.add_argument("--effect-json",type=Path,required=True);a.add_argument("--binding-out",type=Path,required=True);a.add_argument("--dispatch-out",type=Path,required=True);a.add_argument("--session-id",required=True);a.add_argument("--dispatch-id",required=True);a.add_argument("--ttl-seconds",type=int,default=120)
    a.add_argument("--test-mode",action="store_true",help=argparse.SUPPRESS);a.add_argument("--private-key",type=Path,help=argparse.SUPPRESS);a.add_argument("--public-key",type=Path,help=argparse.SUPPRESS);a.add_argument("--test-gh",type=Path,help=argparse.SUPPRESS);a.add_argument("--test-signer",type=Path,help=argparse.SUPPRESS)
    a.set_defaults(func=authorize);return p

def main():
    global _TEST_GH,_TEST_SIGNER
    args=parser().parse_args()
    if args.test_mode:_TEST_GH=args.test_gh;_TEST_SIGNER=args.test_signer
    try:return args.func(args)
    except CoordinatorError as e:print(f"TRUSTED_EXTERNAL_WRITE_COORDINATOR=BLOCK reason={e}");return 3
if __name__=="__main__":raise SystemExit(main())
