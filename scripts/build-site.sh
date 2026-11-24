#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
if [[ ! -x node_modules/.bin/antora ]]; then npm ci --no-audit --no-fund; fi
# Keep the downloadable lab code in sync with the source used for this build.
python3 - <<'PY'
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
root = Path.cwd()
attachment = root / "content/en/modules/ROOT/attachments/lab-files.zip"
attachment.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(attachment, "w", ZIP_DEFLATED) as z:
    for path in [root / "grlab", root / "requirements.txt", root / "LICENSE", root / "NOTICE.md"]:
        z.write(path, "guardrail-agent-lab/" + path.name)
    for folder in ["app", "policies", "deploy", "proxy", "scripts", "tests"]:
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc" and path.name not in {"build-site.sh", "check_portal.py"}:
                z.write(path, "guardrail-agent-lab/" + path.relative_to(root).as_posix())
PY
# Antora reads a Git worktree. Build a disposable one even from a fresh ZIP.
stage=$(mktemp -d "$root/.antora-build.XXXXXXXX")
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/showroom"
cp -R content supplemental-ui site.yml "$stage/"
cp showroom/ui-bundle.zip "$stage/showroom/"
git -C "$stage" init -q
git -C "$stage" add content supplemental-ui site.yml showroom/ui-bundle.zip
git -C "$stage" -c user.name=Showroom -c user.email=build@localhost commit -qm 'Content for the local build'
"$root/node_modules/.bin/antora" --to-dir "$root/showroom/www" "$stage/site.yml"
