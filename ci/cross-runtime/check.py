"""Repeat the two timed-out links using the exact retained cross package."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import url2pathname


def pe_machine(path):
    data = path.read_bytes()
    offset = struct.unpack_from("<I", data, 60)[0]
    assert data[:2] == b"MZ" and data[offset:offset + 4] == b"PE\0\0", path
    return struct.unpack_from("<H", data, offset + 4)[0]


assert pe_machine(Path(sys.executable)) == 0x8664
prefix = Path(sys.prefix)
records = [json.loads(p.read_text()) for p in (prefix / "conda-meta").glob("*.json")]
cross = next(r for r in records if r["name"] == "zig_win-arm64")
assert cross["subdir"] == "win-64" and cross["build"] == "c3f6a10_2033_af24fd11a_1", cross
assert cross.get("url", "").startswith("file:"), cross
package = Path(url2pathname(urlparse(cross["url"]).path))
package_sha = hashlib.sha256(package.read_bytes()).hexdigest()
assert package_sha == "b6c4bd6051298ca1789c065fdc7285fda623fb72abd8ede0a51f69bfcd5c1755", cross
seed = next(r for r in records if r["name"] == "zig_impl_win-64")
assert seed["build"] == "ba77cf0_1970_67f39b551_0", seed
print("ATTESTED cross package:", cross["url"], package_sha, flush=True)
print("ATTESTED public bootstrap:", seed["url"], seed["build"], flush=True)
wrapper = prefix / "Library/bin/aarch64-w64-mingw32-zig-cc.exe"
assert pe_machine(wrapper) == 0x8664
with tempfile.TemporaryDirectory(prefix="zig import library check ") as temp:
    root = Path(temp)
    source = root / "sync_test.c"
    source.write_text("int main(void) { return 0; }\n")
    for library in ("synchronization", "api-ms-win-core-synch-l1-2-0"):
        output = root / (library + ".exe")
        command = [str(wrapper), "-l" + library, "-o", str(output), str(source)]
        print("RUN:", command, flush=True)
        subprocess.run(command, cwd=root, check=True, timeout=300)
        assert pe_machine(output) == 0xAA64, output
        print("PASS: linked ARM64 executable with -l" + library, flush=True)
