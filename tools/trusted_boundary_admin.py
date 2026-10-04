#!/usr/bin/env python3
"""Root-admin bootstrap/verify/upgrade/remove for the fixed Engineering System trust boundary."""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from production_approver_policy import (
    PolicyError,
    canonical_policy_bytes,
    load_policy_bytes,
)

ETC=Path("/etc/engineering-system")
LIB=Path("/usr/lib/engineering-system")
KEY=ETC/"skills-trust-anchor.key"
PUB=ETC/"skills-trust-anchor.pub"
REPLAY=ETC/"skills-replay-state"
APPROVERS=ETC/"production-approvers.json"
INSTALLS={
 "skills-contract.py":"skills-contract.py",
 "trusted_external_write_signer.py":"trusted-external-write-signer",
 "trusted_external_write_coordinator.py":"trusted-external-write-coordinator",
 "trusted_production_write_signer.py":"trusted-production-write-signer",
 "trusted_production_write_coordinator.py":"trusted-production-write-coordinator",
 "execution_profile.py":"execution_profile.py",
}
IMPORTS={
 "trusted_external_write_coordinator.py":"trusted_external_write_coordinator.py",
 "work_packet_authority.py":"work_packet_authority.py",
 "production_approver_policy.py":"production_approver_policy.py",
}
OPENSSL=Path("/usr/bin/openssl")


class BoundaryError(Exception):
    pass


def _root():
    if os.geteuid()!=0:
        raise BoundaryError("root is required")


def _secure_file(path:Path,mode:int):
    st=path.lstat()
    if (
        not stat.S_ISREG(st.st_mode)
        or stat.S_ISLNK(st.st_mode)
        or st.st_uid!=0
        or stat.S_IMODE(st.st_mode)!=mode
    ):
        raise BoundaryError(f"invalid boundary file provenance: {path}")


def _secure_dir(path:Path,mode:int):
    st=path.lstat()
    if (
        not stat.S_ISDIR(st.st_mode)
        or stat.S_ISLNK(st.st_mode)
        or st.st_uid!=0
        or stat.S_IMODE(st.st_mode)!=mode
    ):
        raise BoundaryError(f"invalid boundary directory provenance: {path}")


def _source(args):
    source=args.source.resolve()
    if not source.is_dir() or source.is_symlink():
        raise BoundaryError("canonical source directory is invalid")
    for name in dict.fromkeys((*INSTALLS, *IMPORTS)):
        path=source/"tools"/name
        if not path.is_file() or path.is_symlink():
            raise BoundaryError(f"canonical source missing: {name}")
    return source


def _copy_artifact(source:Path,target:Path,mode:int,*,replace:bool):
    if target.exists() or target.is_symlink():
        if not replace:
            raise BoundaryError(f"boundary artifact already exists: {target}")
        _secure_file(target,mode)
    fd,tmp_name=tempfile.mkstemp(prefix=f".{target.name}.",dir=str(target.parent))
    os.close(fd)
    tmp=Path(tmp_name)
    try:
        shutil.copyfile(source,tmp,follow_symlinks=False)
        os.chown(tmp,0,0)
        os.chmod(tmp,mode)
        _secure_file(tmp,mode)
        os.replace(tmp,target)
    finally:
        if tmp.exists():
            tmp.unlink()


def _copy_tools(source:Path,*,replace:bool):
    for src,dst in (*INSTALLS.items(), *IMPORTS.items()):
        mode=0o755 if dst.startswith("trusted-") else 0o644
        _copy_artifact(source/"tools"/src,LIB/dst,mode,replace=replace)


def _canonical_policy_bytes(path:Path)->bytes:
    try:
        st=path.lstat()
        if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            raise BoundaryError("production approver policy source is invalid")
        raw=path.read_bytes()
        normalized=load_policy_bytes(raw)
        return canonical_policy_bytes(normalized)
    except (OSError,PolicyError) as exc:
        raise BoundaryError(str(exc)) from exc


def _install_policy_bytes(raw:bytes,*,replace:bool):
    if APPROVERS.exists() or APPROVERS.is_symlink():
        if not replace:
            raise BoundaryError("production approver policy already exists")
        _secure_file(APPROVERS,0o600)
    fd,tmp_name=tempfile.mkstemp(prefix=".production-approvers.",dir=str(ETC))
    os.close(fd)
    tmp=Path(tmp_name)
    try:
        tmp.write_bytes(raw)
        os.chown(tmp,0,0)
        os.chmod(tmp,0o600)
        _secure_file(tmp,0o600)
        os.replace(tmp,APPROVERS)
    finally:
        if tmp.exists():
            tmp.unlink()


def _verify_anchor_and_dirs():
    _secure_dir(ETC,0o700)
    _secure_dir(LIB,0o755)
    _secure_dir(REPLAY,0o700)
    _secure_file(KEY,0o600)
    _secure_file(PUB,0o644)
    cp=subprocess.run(
        [str(OPENSSL),"pkey","-pubin","-in",str(PUB),"-noout"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if cp.returncode:
        raise BoundaryError("public trust anchor is invalid")


def install(args):
    _root()
    source=_source(args)
    policy=getattr(args,"production_approver_policy",None)
    policy_bytes=_canonical_policy_bytes(policy) if policy is not None else None
    ETC.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(ETC,0o700)
    LIB.mkdir(parents=True,exist_ok=True,mode=0o755)
    os.chmod(LIB,0o755)
    if KEY.exists() or PUB.exists():
        raise BoundaryError("trust anchor already exists; verify or upgrade instead")
    subprocess.run(
        [str(OPENSSL),"genpkey","-algorithm","ED25519","-out",str(KEY)],
        check=True,
        env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.chmod(KEY,0o600)
    subprocess.run(
        [str(OPENSSL),"pkey","-in",str(KEY),"-pubout","-out",str(PUB)],
        check=True,
        env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.chmod(PUB,0o644)
    REPLAY.mkdir(mode=0o700)
    os.chmod(REPLAY,0o700)
    _copy_tools(source,replace=False)
    if policy_bytes is not None:
        _install_policy_bytes(policy_bytes,replace=False)
    verify(args)
    print("TRUSTED_BOUNDARY_INSTALL=PASS")


def upgrade(args):
    _root()
    source=_source(args)
    policy=getattr(args,"production_approver_policy",None)
    policy_bytes=_canonical_policy_bytes(policy) if policy is not None else None
    _verify_anchor_and_dirs()
    if policy_bytes is None and (APPROVERS.exists() or APPROVERS.is_symlink()):
        _secure_file(APPROVERS,0o600)
        try:
            load_policy_bytes(APPROVERS.read_bytes())
        except (OSError,PolicyError) as exc:
            raise BoundaryError(str(exc)) from exc
    for _,dst in (*INSTALLS.items(), *IMPORTS.items()):
        target=LIB/dst
        if target.exists() or target.is_symlink():
            mode=0o755 if dst.startswith("trusted-") else 0o644
            _secure_file(target,mode)
    _copy_tools(source,replace=True)
    if policy_bytes is not None:
        _install_policy_bytes(policy_bytes,replace=True)
    verify(args)
    print("TRUSTED_BOUNDARY_UPGRADE=PASS")


def verify(args):
    _root()
    _verify_anchor_and_dirs()
    for _,dst in (*INSTALLS.items(), *IMPORTS.items()):
        _secure_file(LIB/dst,0o755 if dst.startswith("trusted-") else 0o644)
    require_policy=bool(getattr(args,"require_production_approvers",False))
    if APPROVERS.exists() or APPROVERS.is_symlink():
        _secure_file(APPROVERS,0o600)
        try:
            load_policy_bytes(APPROVERS.read_bytes())
        except (OSError,PolicyError) as exc:
            raise BoundaryError(str(exc)) from exc
    elif require_policy:
        raise BoundaryError("production approver policy unavailable")
    print("TRUSTED_BOUNDARY_VERIFY=PASS")


def remove(args):
    _root()
    for _,dst in (*INSTALLS.items(), *IMPORTS.items()):
        path=LIB/dst
        if path.exists() or path.is_symlink():
            if path.is_symlink():
                raise BoundaryError("refusing symlink boundary removal")
            path.unlink()
    if APPROVERS.exists() or APPROVERS.is_symlink():
        if APPROVERS.is_symlink():
            raise BoundaryError("refusing symlink production approver policy removal")
        APPROVERS.unlink()
    if REPLAY.exists():
        if REPLAY.is_symlink():
            raise BoundaryError("refusing symlink replay removal")
        shutil.rmtree(REPLAY)
    for path in (PUB,KEY):
        if path.exists():
            if path.is_symlink():
                raise BoundaryError("refusing symlink key removal")
            path.unlink()
    print("TRUSTED_BOUNDARY_REMOVE=PASS")


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="cmd",required=True)
    install_parser=sub.add_parser("install")
    install_parser.add_argument("--source",type=Path,required=True)
    install_parser.add_argument("--production-approver-policy",type=Path)
    install_parser.set_defaults(func=install)
    upgrade_parser=sub.add_parser("upgrade")
    upgrade_parser.add_argument("--source",type=Path,required=True)
    upgrade_parser.add_argument("--production-approver-policy",type=Path)
    upgrade_parser.set_defaults(func=upgrade)
    verify_parser=sub.add_parser("verify")
    verify_parser.add_argument("--require-production-approvers",action="store_true")
    verify_parser.set_defaults(func=verify)
    remove_parser=sub.add_parser("remove")
    remove_parser.set_defaults(func=remove)
    return p


def main():
    args=parser().parse_args()
    try:
        return args.func(args) or 0
    except (BoundaryError,OSError,subprocess.SubprocessError) as exc:
        print(f"TRUSTED_BOUNDARY=BLOCK reason={exc}")
        return 3


if __name__=="__main__":
    raise SystemExit(main())
