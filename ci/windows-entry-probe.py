"""Fork-only counterfactuals for the ARM64 custom-entry link failure."""
import pathlib
import subprocess
import tempfile

wrappers = list(pathlib.Path('C:/bld/test').glob(
    'test_zig_win-arm64*/test_run_env/Library/bin/aarch64-w64-mingw32-zig-cc.exe'
))
if not wrappers:
    raise SystemExit('No retained ARM64-targeting test wrapper found')
wrapper = wrappers[-1]
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    source = root / 'entry.c'
    source.write_text('#include <windows.h>\nvoid MyEntry(void) { ExitProcess(0); }\n')
    for label, flags in [
        ('baseline', []),
        ('dynamic-crt', ['-fms-runtime-lib=dll']),
        ('explicit-libc', ['-lc']),
        ('explicit-ucrt', ['-lucrt']),
        ('no-start-files', ['-nostartfiles']),
        ('no-crt', ['-nostdlib', '-lkernel32']),
    ]:
        command = [str(wrapper), '-v', '-Wl,-eMyEntry', '-Wl,--subsystem,console',
                   str(source), '-o', str(root / (label + '.exe')), *flags]
        print('PROBE', label, command, flush=True)
        result = subprocess.run(command, capture_output=True, text=True)
        print('EXIT', result.returncode, '\nSTDOUT\n', result.stdout,
              '\nSTDERR\n', result.stderr, flush=True)
