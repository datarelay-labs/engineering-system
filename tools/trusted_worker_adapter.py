#!/usr/bin/env python3
from __future__ import annotations
import argparse,os,stat,subprocess
from pathlib import Path
WORKER=Path("/usr/lib/engineering-system/worker-adapter")
PYTHON=Path("/usr/bin/python3")
def trusted(path):
    try:
        lst=path.lstat()
        target=path.resolve(strict=True)
        st=target.stat(); par=target.parent.stat()
        return (stat.S_ISREG(st.st_mode) and st.st_uid==0 and not(st.st_mode&0o022)
                and par.st_uid==0 and not(par.st_mode&0o022)
                and (stat.S_ISREG(lst.st_mode) or stat.S_ISLNK(lst.st_mode)))
    except OSError:return False
def main():
    ap=argparse.ArgumentParser();ap.add_argument("command",choices=("evaluate",));ap.add_argument("--request-json",type=Path,required=True);a=ap.parse_args()
    if os.geteuid()!=0:
        print("TRUSTED_WORKER_ADAPTER=BLOCK reason=root_required");return 3
    if not trusted(WORKER) or not trusted(PYTHON):
        print("TRUSTED_WORKER_ADAPTER=BLOCK reason=trusted_runtime");return 3
    try:
        st=a.request_json.lstat()
        if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode) or st.st_size>1024*1024:
            raise OSError()
    except OSError:
        print("TRUSTED_WORKER_ADAPTER=BLOCK reason=request");return 3
    try:
        import json
        payload=json.loads(a.request_json.read_text(encoding="utf-8"))
        binding_path=Path(payload["verification"]["binding_assertion"])
        binding=json.loads(binding_path.read_text(encoding="utf-8"))
        worktree=binding["scope"]["worktree"]
        if not isinstance(worktree,str) or not Path(worktree).is_absolute():
            raise ValueError()
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError):
        print("TRUSTED_WORKER_ADAPTER=BLOCK reason=worktree");return 3
    cp=subprocess.run([str(PYTHON),str(WORKER),"evaluate","--request-json",str(a.request_json)],cwd=worktree,env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C"},text=True)
    return cp.returncode
if __name__=="__main__":raise SystemExit(main())
