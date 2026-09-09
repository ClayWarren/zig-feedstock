"""Bounded reproductions of the retained Windows package test warnings."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse
from urllib.request import url2pathname
import pefile

root = Path.cwd() / 'warning-evidence'
root.mkdir(exist_ok=True)
target = os.environ['WARNING_TARGET']
triplet, machine, zig_target = {'win-64': ('x86_64-w64-mingw32', 0x8664, 'x86_64-windows-gnu'),
                               'win-32': ('i686-w64-mingw32', 0x14c, 'x86-windows-gnu')}[target]
prefix = Path(sys.prefix)
assert pefile.PE(sys.executable).FILE_HEADER.Machine == 0x8664
records = [json.loads(p.read_text()) for p in (prefix/'conda-meta').glob('*.json')]
for record in records:
    if record['name'].startswith('zig'):
        print('PACKAGE:', record['name'], record['build'], record.get('url'), flush=True)
        if record['name'] == 'zig_' + target:
            assert record['build_number'] == 1 and '2033_af24fd11a_1' in record['build'], record
            assert record['url'].startswith('file:'), record
            file = Path(url2pathname(urlparse(record['url']).path))
            print('SHA256:', hashlib.sha256(file.read_bytes()).hexdigest(), flush=True)
        if record['name'] == 'zig_impl_win-64':
            expected = 'ba77cf0_2033_af24fd11a_1' if target == 'win-64' else 'ba77cf0_1970_67f39b551_0'
            assert record['build'] == expected, record
compiler = prefix/'Library/bin/x86_64-w64-mingw32-zig.exe'
wrapper = prefix/'Library/bin'/f'{triplet}-zig-cc.exe'
results = []
def run(label, args, bound=600):
    print('RUN:', label, args, flush=True)
    start = time.monotonic()
    try:
        result = subprocess.run([str(a) for a in args], capture_output=True, text=True, errors='replace', timeout=bound)
        rc, output = result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:
        rc, output = -1, f'TIMEOUT after {bound}s\n' + str(exc.stdout) + str(exc.stderr)
    elapsed = time.monotonic() - start
    (root/(label+'.log')).write_text(output, encoding='utf-8')
    results.append({'label': label, 'returncode': rc, 'seconds': elapsed})
    print('RESULT:', results[-1], output[-8000:], flush=True)
    return rc

def inspect(path, expected):
    pe = pefile.PE(str(path))
    assert pe.FILE_HEADER.Machine == expected, path
    imports = [entry.dll.decode() for entry in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', [])]
    print('PE:', path, hex(expected), 'IMPORTS:', imports, flush=True)
    return imports

source = root/'sync.c';source.write_text('int main(void) { return 0; }\n')
for mode, extra in [('default', []), ('explicit-gnu', ['-target', zig_target])]:
    output = root/(mode+'.exe')
    rc = run('api-set-'+mode, [wrapper, *extra, '-v', '-lapi-ms-win-core-synch-l1-2-0', '-o', output, source],300)
    if rc == 0:
        inspect(output,machine)
        run('execute-'+mode,[output],30)

if target == 'win-64':
    source = root/'cxxlib.cpp'
    source.write_text('#include <string>\n#include <typeinfo>\nextern "C" {\n  __attribute__((visibility("default")))\n  const char* cxx_rtti(void) { return typeid(std::string).name(); }\n}\n')
    output = root/'cxxtest.dll'
    for shared in (prefix/'Library/lib/libc++.dll.a', prefix/'Library/lib/zig-llvm/lib/libc++.dll.a'):
        assert not shared.exists(), shared
    for label in ('libcxx-cold', 'libcxx-warm'):
        rc=run(label,[compiler,'c++','-shared','-o',output,source])
        if rc == 0:
            imports=inspect(output,0x8664)
            assert not any('libc++' in name.lower() for name in imports),imports
        else:
            break
    consumer=root/'cxx-consumer.cpp';consumer.write_text('#include <string>\nint main() { std::string s="static libcxx"; s += " OK"; return s.size() == 16 ? 0 : 1; }\n')
    executable=root/'cxx-consumer.exe'
    if run('libcxx-consumer-build',[compiler,'c++',consumer,'-o',executable]) == 0:
        inspect(executable,0x8664)
        run('libcxx-consumer-run',[executable],30)
(root/'results.json').write_text(json.dumps(results,indent=2))
required = ['api-set-explicit-gnu', 'execute-explicit-gnu']
if target == 'win-64': required += ['libcxx-cold','libcxx-warm','libcxx-consumer-build','libcxx-consumer-run']
assert all(any(r['label']==name and r['returncode']==0 for r in results) for name in required), results
print('PASS: focused warning diagnostics; original default API-set result retained separately',flush=True)
