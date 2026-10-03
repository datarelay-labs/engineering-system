#!/usr/bin/env python3
"""Root-admin bootstrap/verify/remove for the fixed Engineering System trust boundary."""
from __future__ import annotations
import argparse, os, shutil, stat, subprocess, sys
from pathlib import Path

ETC=Path("/etc/engineering-system")
LIB=Path("/usr/lib/engineering-system")
KEY=ETC/"skills-trust-anchor.key"
PUB=ETC/"skills-trust-anchor.pub"
REPLAY=ETC/"skills-replay-state"
INSTALLS={
 "skills-contract.py":"skills-contract.py",
 "trusted_external_write_signer.py":"trusted-external-write-signer",
 "trusted_external_write_coordinator.py":"trusted_external_write_coordinator.py",
 "trusted_production_write_signer.py":"trusted-production-write-signer",
 "trusted_production_write_coordinator.py":"trusted-production-write-coordinator",
 "execution_profile.py":"execution_profile.py",
}
OPENSSL=Path("/usr/bin/openssl")

class BoundaryError(Exception): pass

def _root():
    if os.geteuid()!=0: raise BoundaryError("root is required")
def _secure_file(path:Path,mode:int):
    st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or stat.S_IMODE(st.st_mode)!=mode:
        raise BoundaryError(f"invalid boundary file provenance: {path}")
def _secure_dir(path:Path,mode:int):
    st=path.lstat()
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or stat.S_IMODE(st.st_mode)!=mode:
        raise BoundaryError(f"invalid boundary directory provenance: {path}")

def install(args):
    _root()
    source=args.source.resolve()
    if not source.is_dir() or source.is_symlink(): raise BoundaryError("canonical source directory is invalid")
    for name in INSTALLS:
        p=source/"tools"/name
        if not p.is_file() or p.is_symlink(): raise BoundaryError(f"canonical source missing: {name}")
    ETC.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(ETC,0o700)
    LIB.mkdir(parents=True,exist_ok=True,mode=0o755); os.chmod(LIB,0o755)
    if KEY.exists() or PUB.exists(): raise BoundaryError("trust anchor already exists; verify or remove instead")
    subprocess.run([str(OPENSSL),"genpkey","-algorithm","ED25519","-out",str(KEY)],check=True,
                   env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C"},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    os.chmod(KEY,0o600)
    subprocess.run([str(OPENSSL),"pkey","-in",str(KEY),"-pubout","-out",str(PUB)],check=True,
                   env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C"},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    os.chmod(PUB,0o644)
    REPLAY.mkdir(mode=0o700); os.chmod(REPLAY,0o700)
    for src,dst in INSTALLS.items():
        target=LIB/dst
        if target.exists(): raise BoundaryError(f"boundary executable already exists: {target}")
        shutil.copyfile(source/"tools"/src,target,follow_symlinks=False)
        os.chown(target,0,0); os.chmod(target,0o755 if dst.startswith("trusted-") else 0o644)
    verify(args)
    print("TRUSTED_BOUNDARY_INSTALL=PASS")

def verify(args):
    _root(); _secure_dir(ETC,0o700); _secure_dir(LIB,0o755); _secure_dir(REPLAY,0o700)
    _secure_file(KEY,0o600); _secure_file(PUB,0o644)
    for _,dst in INSTALLS.items(): _secure_file(LIB/dst,0o755 if dst.startswith("trusted-") else 0o644)
    cp=subprocess.run([str(OPENSSL),"pkey","-pubin","-in",str(PUB),"-noout"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if cp.returncode: raise BoundaryError("public trust anchor is invalid")
    print("TRUSTED_BOUNDARY_VERIFY=PASS")

def remove(args):
    _root()
    for _,dst in INSTALLS.items():
        p=LIB/dst
        if p.exists():
            if p.is_symlink(): raise BoundaryError("refusing symlink boundary removal")
            p.unlink()
    if REPLAY.exists():
        if REPLAY.is_symlink(): raise BoundaryError("refusing symlink replay removal")
        shutil.rmtree(REPLAY)
    for p in (PUB,KEY):
        if p.exists():
            if p.is_symlink(): raise BoundaryError("refusing symlink key removal")
            p.unlink()
    print("TRUSTED_BOUNDARY_REMOVE=PASS")

def parser():
    p=argparse.ArgumentParser(description=__doc__); s=p.add_subparsers(dest="cmd",required=True)
    i=s.add_parser("install"); i.add_argument("--source",type=Path,required=True); i.set_defaults(func=install)
    v=s.add_parser("verify"); v.set_defaults(func=verify)
    r=s.add_parser("remove"); r.set_defaults(func=remove)
    return p
def main():
    a=parser().parse_args()
    try:return a.func(a) or 0
    except (BoundaryError,OSError,subprocess.SubprocessError) as e:
        print(f"TRUSTED_BOUNDARY=BLOCK reason={e}"); return 3
if __name__=="__main__": raise SystemExit(main())
