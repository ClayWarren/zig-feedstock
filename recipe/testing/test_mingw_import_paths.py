"""Host-side regression checks for the existing import-library assertions."""

import ast
import contextlib
import io
import tempfile
import unittest
from pathlib import Path


class ImportLibraryPaths(unittest.TestCase):
    def probe(self, arch, leaf, *, windows=True, empty=False):
        # Isolate the actual assertion function from toolchain environment setup.
        source_path = Path(__file__).with_name('test_zig_toolchain.py')
        source = source_path.read_text(encoding='utf-8')
        function = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == 'test_mingw_prebuilt_import_libs')
        results = {'PASS': [], 'FAIL': [], 'SKIP': []}
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            root = prefix / 'Library' if windows else prefix
            directory = root / 'lib' / 'zig' / 'libc' / 'mingw' / leaf
            directory.mkdir(parents=True)
            for name in ('ws2_32', 'kernel32', 'ole32', 'advapi32', 'user32',
                         'uuid', 'synchronization', 'shlwapi', 'version'):
                (directory / f'lib{name}.a').write_bytes(b'' if empty else b'fixture')
            namespace = {'Path': Path, '_prefix': prefix, '_arch': arch,
                         '_build_is_win': windows, 'is_win_target': True}
            for kind in results:
                namespace[kind] = lambda *args, kind=kind: results[kind].append(args)
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(source_path), 'exec'), namespace)
            with contextlib.redirect_stdout(io.StringIO()):
                namespace['test_mingw_prebuilt_import_libs']()
        return results

    def test_target_directories_on_both_build_platforms(self):
        for windows in (False, True):
            for arch, leaf in (('aarch64', 'libarm64'), ('x86_64', 'lib-common'),
                               ('x86', 'lib32'), ('i386', 'lib32'), ('i686', 'lib32')):
                with self.subTest(windows=windows, arch=arch):
                    results = self.probe(arch, leaf, windows=windows)
                    self.assertEqual(len(results['PASS']), 10)
                    self.assertEqual(results['FAIL'], [])
                    self.assertEqual(results['SKIP'], [])

    def test_wrong_architecture_directory_does_not_pass(self):
        results = self.probe('aarch64', 'lib-common')
        self.assertEqual(len(results['FAIL']), 1)
        self.assertEqual(results['PASS'], [])

    def test_empty_archives_still_fail(self):
        results = self.probe('aarch64', 'libarm64', empty=True)
        self.assertEqual(len(results['FAIL']), 9)


if __name__ == '__main__':
    unittest.main()
