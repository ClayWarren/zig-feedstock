"""Compare ordinary C compilation using stock and feedstock-overlay libraries.

Fork-only diagnosis, not package acceptance. No specialized upstream crash
reproducers are used. Failures are retained and do not prevent the other lane.
"""

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from accept import check_pe


EVIDENCE = Path('evidence').resolve()
BUILD = Path('C:/bld')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def invoke(name, command, *, env=None, timeout=300):
    command = [str(arg) for arg in command]
    print('+', subprocess.list2cmdline(command), flush=True)
    start = time.monotonic()
    result = {'command': command}
    with (EVIDENCE / f'{name}.log').open('w', encoding='utf-8') as log:
        try:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                       env=env, timeout=timeout, check=False)
            result['returncode'] = completed.returncode
            result['exit_hex'] = f'0x{completed.returncode & 0xffffffff:08X}'
        except subprocess.TimeoutExpired:
            result['returncode'] = None
            result['timed_out'] = True
    result['seconds'] = round(time.monotonic() - start, 3)
    (EVIDENCE / f'{name}.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(name, result, flush=True)
    return result


def smoke(label, zig, library):
    check_pe(zig)
    lane = {'compiler': str(zig), 'sha256': digest(zig), 'library': str(library)}
    # Retain a content manifest, not the large compiler/library payload itself.
    manifest = {str(path.relative_to(library)): digest(path)
                for path in sorted(library.rglob('*')) if path.is_file()}
    (EVIDENCE / f'{label}-library.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    lane['version'] = invoke(f'{label}-version', [zig, 'version'])
    source = EVIDENCE / 'smoke.c'
    for step in ('object', 'link'):
        work = Path('C:/seed-comparison') / label / step
        work.mkdir(parents=True, exist_ok=False)
        env = os.environ.copy()
        env['ZIG_GLOBAL_CACHE_DIR'] = str(work / 'global')
        env['ZIG_LOCAL_CACHE_DIR'] = str(work / 'local')
        # Zig 0.16's cc argument parser rejects --zig-lib-dir; the compiler
        # supports the equivalent environment override for this subcommand.
        env['ZIG_LIB_DIR'] = str(library)
        output = work / ('smoke.obj' if step == 'object' else 'smoke.exe')
        command = [zig, 'cc', '-target', 'aarch64-windows-gnu']
        if step == 'object':
            command.append('-c')
        command += [source, '-o', output]
        lane[step] = invoke(f'{label}-{step}', command, env=env)
        lane[step]['environment'] = {key: env[key] for key in ('ZIG_GLOBAL_CACHE_DIR', 'ZIG_LOCAL_CACHE_DIR', 'ZIG_LIB_DIR')}
        if lane[step]['returncode'] == 0:
            if not output.is_file():
                raise RuntimeError(f'compiler reported success without {output}')
            lane[step]['output_sha256'] = digest(output)
            if step == 'link':
                check_pe(output)
                lane['run'] = invoke(f'{label}-run', [output], env=env)
    return lane


def main():
    if sys.platform != 'win32' or platform.machine().lower() not in {'arm64', 'aarch64'}:
        raise RuntimeError('native Windows ARM64 Python required')
    check_pe(sys.executable)
    EVIDENCE.mkdir(exist_ok=True)
    records = [json.loads(path.read_text()) for path in (Path(sys.prefix) / 'conda-meta').glob('*.json')]
    if not records or any(record['subdir'] not in {'win-arm64', 'noarch'} for record in records):
        raise RuntimeError('unexpected environment architecture')
    (EVIDENCE / 'installed-records.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    (EVIDENCE / 'smoke.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
    build = invoke('candidate-build', [
        'rattler-build', 'build', '--recipe', 'recipe/recipe.yaml',
        '--target-platform', 'win-arm64', '--build-platform', 'win-arm64',
        '-m', '.ci_support/win_arm64_cross_target_platform_win-arm64.yaml',
        '--no-config', '--keep-build', '--output-dir', BUILD,
    ], timeout=2400)
    stock = [path for path in BUILD.rglob('zig.exe') if 'zig-native-seed' in path.parts]
    patched = list(BUILD.rglob('aarch64-w64-mingw32-zig.exe'))
    if len(stock) != 1 or len(patched) != 1:
        raise RuntimeError(f'expected one stock and one installed seed: {stock=}, {patched=}')
    if digest(stock[0]) != digest(patched[0]):
        raise RuntimeError('stock and installed compiler binaries differ')
    lanes = {
        'stock': smoke('stock', stock[0], stock[0].parent / 'lib'),
        'patched': smoke('patched', patched[0], patched[0].parent.parent / 'lib' / 'zig'),
    }
    report = {'machine': platform.machine(), 'candidate_build': build, 'lanes': lanes}
    (EVIDENCE / 'comparison.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
    # A completed diagnostic must not make failing compilation appear green.
    return int(build['returncode'] != 0 or any(
        lane.get(step, {}).get('returncode') != 0
        for lane in lanes.values() for step in ('version', 'object', 'link', 'run')
    ))


if __name__ == '__main__':
    sys.exit(main())
