"""Fork-only counterfactuals for the ARM64 custom-entry link failure."""
import pathlib
import subprocess
import tempfile
import sys
import shutil

wrappers = list(pathlib.Path('C:/bld/test').glob(
    'test_zig_win-arm64*/test_run_env/Library/bin/aarch64-w64-mingw32-zig-cc.exe'
))
if not wrappers:
    raise SystemExit('No retained ARM64-targeting test wrapper found')
wrapper = wrappers[-1]
if '--activated' not in sys.argv:
    test_dir = wrapper.parents[3] / 'test'
    activation = test_dir / 'build_env.bat'
    if not activation.is_file():
        raise SystemExit(f'Missing retained test activation: {activation}')
    command = f'call "{activation}" && python "{pathlib.Path(__file__).resolve()}" --activated'
    # Pass cmd's command tail verbatim: list2cmdline escapes embedded quotes
    # with backslashes, which cmd.exe treats as literal path characters.
    raise SystemExit(subprocess.call('cmd.exe /d /s /c "' + command + '"', cwd=test_dir))
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    source = root / 'entry.c'
    source.write_text('#include <windows.h>\nvoid MyEntry(void) { ExitProcess(0); }\n')
    # Zig's -l discovery omits the SDK ucrt directory even though LLD's
    # emitted -LIBPATH includes it. Use verified absolute files to separate
    # library discovery from the missing automatic link dependency.
    sdk_libs = sorted(pathlib.Path('C:/Program Files (x86)/Windows Kits/10/Lib').glob('*/ucrt/arm64/libucrt.lib'))
    if not sdk_libs:
        raise SystemExit('No ARM64 SDK static UCRT found')
    static_ucrt = sdk_libs[-1]
    print('SDK UCRT:', static_ucrt, flush=True)
    for name in ('libucrt.lib', 'ucrt.lib'):
        shutil.copyfile(static_ucrt.with_name(name), root / name)
    for label, flags in [
        ('baseline', []),
        ('dynamic-crt', ['-fms-runtime-lib=dll']),
        ('explicit-libc', ['-lc']),
        ('explicit-ucrt', ['-lucrt']),
        ('explicit-static-ucrt', ['-llibucrt']),
        ('absolute-static-ucrt', [str(static_ucrt)]),
        ('absolute-dynamic-ucrt', [str(static_ucrt.with_name('ucrt.lib'))]),
        ('spaceless-static-ucrt', [str(root / 'libucrt.lib')]),
        ('spaceless-dynamic-ucrt', [str(root / 'ucrt.lib')]),
        ('no-start-files', ['-nostartfiles']),
        ('no-crt', ['-nostdlib', '-lkernel32']),
    ]:
        command = [str(wrapper), '-v', '-Wl,-eMyEntry', '-Wl,--subsystem,console',
                   str(source), '-o', str(root / (label + '.exe')), *flags]
        print('PROBE', label, command, flush=True)
        result = subprocess.run(command, capture_output=True, text=True)
        print('EXIT', result.returncode, '\nSTDOUT\n', result.stdout,
              '\nSTDERR\n', result.stderr, flush=True)
    # Bypass both _spawnv wrappers to test the same SDK file with spaces.
    raw = wrapper.with_name('x86_64-w64-mingw32-zig.exe')
    command = [str(raw), 'cc', '-target', 'aarch64-windows-msvc', '-v',
               '-Wl,--entry,MyEntry', '-Wl,--subsystem,console', str(source),
               '-o', str(root / 'raw-static.exe'), str(static_ucrt)]
    print('PROBE raw-static-ucrt', command, flush=True)
    result = subprocess.run(command, capture_output=True, text=True)
    print('EXIT', result.returncode, '\nSTDOUT\n', result.stdout,
          '\nSTDERR\n', result.stderr, flush=True)
