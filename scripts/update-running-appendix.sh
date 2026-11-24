#!/usr/bin/env bash
# Upgrade the current participant workspace. The instructor runs this from the new full repository.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
: "${LAB_NAMESPACE:?Set LAB_NAMESPACE to the project of the running Showroom}"
oc -n "$LAB_NAMESPACE" exec -i deployment/grlab-showroom -c terminal -- python3 -c '
import ast, os, stat, sys, tempfile
from pathlib import Path
root=Path("/workspace/lab")
def replace(path, data, mode):
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as f:
        tmp=Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.chmod(mode)
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)
entry=root/"scripts/lab.py"
code=entry.read_text()
before="from catalog_cli import run"
after="from appendix import run"
if after not in code and code.count(before)!=1:
    sys.exit("Use a participant workspace from the category-picker edition of this workshop.")
for name in ("catalog_cli.py","rulebook.py","rule_sample.py","policy.py"):
    if not (entry.parent/name).is_file():
        sys.exit("Missing participant file "+name)
source=sys.stdin.buffer.read()
ast.parse(source)
target=entry.parent/"appendix.py"
new_code=code if after in code else code.replace(before,after,1)
ast.parse(new_code)
replace(target,source,0o644)
if code!=new_code:
    replace(entry,new_code.encode(),stat.S_IMODE(entry.stat().st_mode))
print("Updated the current Terminal. Rule selection and running workloads were preserved.")
' < "$root/scripts/appendix.py"
oc -n "$LAB_NAMESPACE" exec deployment/grlab-showroom -c terminal -- /bin/bash -ec 'cd /workspace/lab && ./grlab catalog apply --local --dry-run'
