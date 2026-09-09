"""Execute ARM64 cross wrappers and verify their x64/x86 output architecture."""
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import url2pathname


def pe(path, expected):
    with path.open('rb') as stream:
        header = stream.read(64)
        assert header[:2] == b'MZ', path
        stream.seek(struct.unpack_from('<I', header, 60)[0])
        signature, machine = struct.unpack('<4sH', stream.read(6))
        assert signature == b'PE\0\0' and machine == expected, (path, hex(machine))
    print('PE:', path, hex(machine), flush=True)


def run(*args):
    print('RUN:', args, flush=True)
    subprocess.run(args, check=True, timeout=300)


target = os.environ['CROSS_TARGET']
triplet, machine = {'win-64': ('x86_64-w64-mingw32', 0x8664),
                    'win-32': ('i686-w64-mingw32', 0x14c)}[target]
assert platform.system() == 'Windows' and platform.machine().lower() in ('arm64', 'aarch64')
pe(Path(sys.executable), 0xaa64)
prefix = Path(sys.prefix)
expected = {'zig_impl_win-arm64', 'zig_' + target}
seen = set()
for path in (prefix / 'conda-meta').glob('*.json'):
    record = json.loads(path.read_text())
    if not record['name'].startswith('zig'):
        continue
    assert record['name'] in expected, record
    assert record['subdir'] == 'win-arm64' and record['build_number'] == 1, record
    assert '2056_79a9897cd_1' in record['build'], record
    assert record.get('url', '').startswith('file:'), record
    local = Path(url2pathname(urlparse(record['url']).path))
    print('CANDIDATE:', record['name'], record['build'], hashlib.sha256(local.read_bytes()).hexdigest(), flush=True)
    if record['name'] == 'zig_' + target:
        assert 'zig_impl_win-arm64 ==0.17.0 *_2056_79a9897cd_1' in record['depends'], record
    seen.add(record['name'])
assert seen == expected, seen
bin_dir = prefix / 'Library' / 'bin'
compiler = bin_dir / 'aarch64-w64-mingw32-zig.exe'
pe(compiler, 0xaa64)
wrappers = [bin_dir / (t + '-zig-' + tool + '.exe')
            for t in ('aarch64-w64-mingw32', triplet)
            for tool in ('cc', 'cxx', 'ar', 'ranlib', 'rc', 'lld', 'asm', 'windres')]
cross = bin_dir / (triplet + '-zig.exe')
for wrapper in wrappers + [cross]:
    pe(wrapper, 0xaa64)
run(str(compiler), 'version')
run(str(cross), 'version')
with tempfile.TemporaryDirectory(prefix='zig ARM64 cross acceptance ') as tmp:
    root = Path(tmp)
    source = root / 'hello.c'
    source.write_text('int main(void) { return 0; }\n')
    for label, command in [('cross', [str(cross), 'cc']),
                           ('cc-wrapper', [str(bin_dir / (triplet + '-zig-cc.exe'))])]:
        output = root / (label + '.exe')
        run(*command, str(source), '-o', str(output))
        pe(output, machine)
    source = root / 'main.zig'
    source.write_text('pub fn main() void {}\n')
    output = root / 'zig-consumer.exe'
    run(str(cross), 'build-exe', str(source), '-femit-bin=' + str(output))
    pe(output, machine)
print('PASS: native ARM64 cross wrappers emitted C and Zig executables for', target, flush=True)
