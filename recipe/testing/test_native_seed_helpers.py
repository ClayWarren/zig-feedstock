"""Host-side unit tests; these do not substitute for native package execution."""

from __future__ import annotations

import importlib.util
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_SPEC = importlib.util.spec_from_file_location(
    "install_native_seed", Path(__file__).resolve().parents[1] / "install_native_seed.py"
)
assert _SPEC is not None and _SPEC.loader is not None
seed = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seed)


class NativeSeedHelpers(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_accepts_arm64_pe_header(self) -> None:
        header = bytearray(134)
        header[:2] = b"MZ"
        struct.pack_into("<I", header, 0x3C, 128)
        header[128:132] = b"PE\0\0"
        struct.pack_into("<H", header, 132, 0xAA64)
        binary = self.root / "header.bin"
        binary.write_bytes(header)
        seed.assert_arm64_pe(binary)

    def test_rejects_x64_pe_header(self) -> None:
        header = bytearray(134)
        header[:2] = b"MZ"
        struct.pack_into("<I", header, 0x3C, 128)
        header[128:132] = b"PE\0\0"
        struct.pack_into("<H", header, 132, 0x8664)
        binary = self.root / "header.bin"
        binary.write_bytes(header)
        with self.assertRaisesRegex(RuntimeError, "expected ARM64 PE"):
            seed.assert_arm64_pe(binary)

    def test_manifest_search_spans_read_chunks(self) -> None:
        binary = self.root / "manifest.bin"
        binary.write_bytes(b"x" * (1024 * 1024 - 4) + b"crt/crt_handler.c")
        self.assertTrue(seed.binary_contains(binary, b"crt/crt_handler.c"))
        self.assertFalse(seed.binary_contains(binary, b"not-present"))

    def test_seed_root_requires_exactly_one_compiler(self) -> None:
        parent = self.root / "zig-native-seed" / "archive"
        parent.mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, "expected one native Zig"):
            seed.find_seed_root(self.root)
        (parent / "zig.exe").touch()
        self.assertEqual(seed.find_seed_root(self.root), parent)
        (parent / "nested").mkdir()
        (parent / "nested" / "zig.exe").touch()
        with self.assertRaisesRegex(RuntimeError, "expected one native Zig"):
            seed.find_seed_root(self.root)

    def test_definition_uses_declared_dll_name(self) -> None:
        definition = self.root / "example.def"
        definition.write_text('LIBRARY "example.dll"\nEXPORTS\nDemo\n')
        self.assertEqual(seed.definition_dll(definition, "fallback"), "example.dll")
        definition.write_text("EXPORTS\nDemo\n")
        self.assertEqual(seed.definition_dll(definition, "fallback"), "fallback.dll")

    def test_fpreset_graft_is_guarded_and_not_repeatable(self) -> None:
        binary = self.root / "manifest.bin"
        binary.write_bytes(b"crt/crt_handler.c")
        library = self.root / "lib"
        crt = library / "libc" / "mingw" / "crt"
        crt.mkdir(parents=True)
        carrier = crt / "crt_handler.c"
        carrier.write_text("/* original source */\n")
        payload = "void _fpreset(void) {}\nvoid fpreset(void) {}\n"
        (crt / "fpreset_arm64.c").write_text(payload)
        seed.graft_seed_fpreset(binary, library)
        result = carrier.read_text()
        self.assertTrue(result.startswith("/* original source */\n"))
        self.assertIn("#if defined(__aarch64__)\n" + payload + "#endif", result)
        with self.assertRaisesRegex(RuntimeError, "already modified"):
            seed.graft_seed_fpreset(binary, library)

    def test_wrong_target_rejected_before_install(self) -> None:
        with patch.dict(os.environ, {"target_platform": "win-64"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "target_platform=win-arm64"):
                seed.main()

    def test_foreign_host_rejected_before_install(self) -> None:
        with patch.dict(os.environ, {"target_platform": "win-arm64"}, clear=True):
            with patch.object(seed.sys, "platform", "darwin"):
                with self.assertRaisesRegex(RuntimeError, "native Windows ARM64 process"):
                    seed.main()

    def test_x64_process_rejected_before_install(self) -> None:
        with patch.dict(os.environ, {"target_platform": "win-arm64"}, clear=True):
            with patch.object(seed.sys, "platform", "win32"):
                with patch.object(seed.platform, "machine", return_value="AMD64"):
                    with self.assertRaisesRegex(RuntimeError, "native Windows ARM64 process"):
                        seed.main()


if __name__ == "__main__":
    unittest.main()
