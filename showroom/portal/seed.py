"""Stage the participant code from the rendered guide into the pod workspace."""
from pathlib import Path
from zipfile import ZipFile
import os
import shutil
import tempfile

def seed(archive, workspace):
    target = workspace / "lab"
    if target.exists():
        return
    with tempfile.TemporaryDirectory(dir=workspace) as tmp:
        stage = Path(tmp) / "lab"
        with ZipFile(archive) as bundle:
            for item in bundle.infolist():
                prefix = "guardrail-agent-lab/"
                path = Path(item.filename.removeprefix(prefix))
                if not item.filename.startswith(prefix) or path.is_absolute() or ".." in path.parts:
                    raise ValueError("Invalid lab archive path")
                dest = stage / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                if item.is_dir():
                    dest.mkdir(exist_ok=True)
                    continue
                with bundle.open(item) as source, dest.open("wb") as out:
                    shutil.copyfileobj(source, out)
                dest.chmod(0o755 if (item.external_attr >> 16) & 0o111 else 0o644)
        if not os.access(stage / "grlab", os.X_OK):
            raise RuntimeError("Participant bundle is missing the executable CLI")
        stage.rename(target)

if __name__ == "__main__":
    seed(Path("/opt/showroom/www/modules/en/_attachments/lab-files.zip"), Path("/workspace"))
