"""Install the checksummed, static upstream ttyd binary for this image architecture."""
import hashlib
import platform
from pathlib import Path
from urllib.request import urlopen

digests = {
    "x86_64": "8a217c968aba172e0dbf3f34447218dc015bc4d5e59bf51db2f2cd12b7be4f55",
    "aarch64": "b38acadd89d1d396a0f5649aa52c539edbad07f4bc7348b27b4f4b7219dd4165",
    "s390x": "bd9b52d6eb0f12cf30902f7dcdeabbcb707a359b18a0203d05ed81d3890ecff8",
}
arch = platform.machine()
if arch not in digests:
    raise SystemExit(f"No configured ttyd artifact for {arch}")
with urlopen(f"https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.{arch}", timeout=60) as response:
    binary = response.read()
if hashlib.sha256(binary).hexdigest() != digests[arch]:
    raise SystemExit("ttyd checksum mismatch")
out = Path("/usr/local/bin/ttyd")
out.write_bytes(binary)
out.chmod(0o755)
