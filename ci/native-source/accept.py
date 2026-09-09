"""Native acceptance of the exact source-built Windows ARM64 candidates."""
import json
import pathlib
import platform
import struct
import subprocess
import sys
import tempfile


def assert_arm64_pe(path):
    with path.open('rb') as stream:
        header = stream.read(64)
        assert header[:2] == b'MZ', path
        stream.seek(struct.unpack_from('<I', header, 0x3c)[0])
        signature, machine = struct.unpack('<4sH', stream.read(6))
        assert signature == b'PE\0\0' and machine == 0xaa64, (path, machine)
    print('ARM64 PE:', path, flush=True)


def run(*command):
    print('RUN:', command, flush=True)
    subprocess.run(command, check=True)


assert platform.system() == 'Windows'
assert platform.machine().lower() in ('arm64', 'aarch64'), platform.machine()
assert_arm64_pe(pathlib.Path(sys.executable))
prefix = pathlib.Path(sys.prefix)
expected = {'zig_impl_win-arm64', 'zig_win-arm64', 'zig', 'zig-compiler'}
seen = set()
for path in (prefix / 'conda-meta').glob('*.json'):
    record = json.loads(path.read_text())
    if record['name'] in expected:
        assert record['subdir'] == 'win-arm64', record
        assert '2033_af24fd11a_1' in record['build'], record
        assert record['build_number'] == 1, record
        assert record.get('url', '').startswith('file:'), record
        print('CANDIDATE:', record['name'], record['build'], record['url'], flush=True)
        seen.add(record['name'])
assert seen == expected, seen
bin_dir = prefix / 'Library' / 'bin'
compiler = bin_dir / 'aarch64-w64-mingw32-zig.exe'
wrapper = bin_dir / 'aarch64-w64-mingw32-zig-cc.exe'
assert_arm64_pe(compiler)
for tool in ('cc', 'cxx', 'ar', 'ranlib', 'rc', 'lld', 'asm', 'windres'):
    assert_arm64_pe(bin_dir / ('aarch64-w64-mingw32-zig-' + tool + '.exe'))
run(str(compiler), 'version')
run(str(compiler), 'env')
run(sys.executable, 'recipe/testing/test_windows_spawn.py', '--zig', str(compiler))
run(sys.executable, 'ci/native-source/spawn-roundtrip.py', str(compiler))
with tempfile.TemporaryDirectory(prefix='zig native acceptance ') as tmp:
    root = pathlib.Path(tmp)
    source = root / 'hello.c'
    source.write_text('#include <stdio.h>\nint main(void) { puts("native ARM64 C OK"); return 0; }\n')
    for name, command in [('raw', [str(compiler), 'cc']), ('wrapper', [str(wrapper)])]:
        output = root / (name + '.exe')
        run(*command, str(source), '-o', str(output))
        assert_arm64_pe(output)
        run(str(output))
    msvc_source = root / 'msvc-entry.c'
    msvc_source.write_text('#include <windows.h>\nvoid MyEntry(void) { ExitProcess(0); }\n')
    msvc_output = root / 'msvc-entry.exe'
    run(str(compiler), 'cc', '-target', 'aarch64-windows-msvc', '-fuse-ld=lld',
        '-Wl,/ENTRY:MyEntry', '-Wl,/SUBSYSTEM:CONSOLE', '-lkernel32',
        str(msvc_source), '-o', str(msvc_output))
    assert_arm64_pe(msvc_output)
    run(str(msvc_output))
    print('PASS: ARM64 MSVC custom-entry link and native execution', flush=True)
    zig_source = root / 'main.zig'
    zig_source.write_text('pub fn main() void {}\n')
    zig_output = root / 'zig-consumer.exe'
    run(str(compiler), 'build-exe', str(zig_source), '-femit-bin=' + str(zig_output))
    assert_arm64_pe(zig_output)
    run(str(zig_output))
print('PASS: source-built compiler and C/Zig consumers executed natively', flush=True)
