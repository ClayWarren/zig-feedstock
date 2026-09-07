#!/usr/bin/env python
"""Install and validate the official native Windows ARM64 Zig seed.

The upstream archive supplies the native compiler, but conda's Zig package also
ships patched MinGW sources and pre-generated libraries used by non-Zig linkers.
Overlay the patched library tree, then materialize the ARM64-only MinGW payload
without attempting a full Zig source build.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


PE_ARM64 = 0xAA64
WINDOWS_TARGET = "aarch64-windows-gnu"
DLLTOOL_MACHINE = "arm64"
SKIP_DEF_TEMPLATES = {
    "crt-aliases",
    "func",
    "ucrtbase-common",
    "vcruntime140-common",
}
REQUIRED_IMPORT_LIBS = {
    "libadvapi32.a",
    "libkernel32.a",
    "libole32.a",
    "libpthread.a",
    "libshlwapi.a",
    "libsynchronization.a",
    "libuser32.a",
    "libuuid.a",
    "libversion.a",
    "libws2_32.a",
}
RUNTIME_ARCHIVE_NAMES = ("libmingw32", "libucrt", "libmingwex", "libwinpthread")
CRT_OBJECTS = ("crt2.o", "crt2win.o", "dllcrt2.o")
SYNCHRONIZATION_DEF = """\
LIBRARY api-ms-win-core-synch-l1-2-0.dll

EXPORTS

DeleteSynchronizationBarrier
EnterSynchronizationBarrier
InitializeConditionVariable
InitializeSynchronizationBarrier
InitOnceBeginInitialize
InitOnceComplete
InitOnceExecuteOnce
InitOnceInitialize
SignalObjectAndWait
Sleep
SleepConditionVariableCS
SleepConditionVariableSRW
WaitOnAddress
WakeAllConditionVariable
WakeByAddressAll
WakeByAddressSingle
WakeConditionVariable
"""
SEED_FPRESET_MARKER = "conda native-seed fpreset carrier"


def assert_arm64_pe(path: Path) -> None:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise RuntimeError(f"not a PE executable: {path}")
        stream.seek(0x3C)
        pe_offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(pe_offset)
        if stream.read(4) != b"PE\0\0":
            raise RuntimeError(f"invalid PE signature: {path}")
        machine = struct.unpack("<H", stream.read(2))[0]
    if machine != PE_ARM64:
        raise RuntimeError(
            f"expected ARM64 PE machine 0x{PE_ARM64:04X}, "
            f"got 0x{machine:04X}: {path}"
        )


def run(
    *args: Path | str,
    capture_output: bool = False,
    env: dict[str, str] | None = None,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [str(arg) for arg in args]
    if not quiet:
        print("+", subprocess.list2cmdline(command), flush=True)
    result = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=capture_output,
        env=env,
    )
    if result.returncode:
        if capture_output:
            if result.stdout:
                print(result.stdout, file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            output=result.stdout if capture_output else None,
            stderr=result.stderr if capture_output else None,
        )
    return result


def find_seed_root(src_dir: Path) -> Path:
    seed_dir = src_dir / "zig-native-seed"
    candidates = [path.parent for path in seed_dir.rglob("zig.exe")]
    if len(candidates) != 1:
        raise RuntimeError(f"expected one native Zig archive root, found {candidates}")
    return candidates[0]


def binary_contains(path: Path, needle: bytes) -> bool:
    """Search a binary without reading the whole compiler into memory."""
    overlap = len(needle) - 1
    tail = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            data = tail + chunk
            if needle in data:
                return True
            tail = data[-overlap:] if overlap else b""
    return False


def graft_seed_fpreset(zig: Path, zig_lib: Path) -> None:
    """Put the patched ARM64 stub in a source the official seed enumerates.

    The source-build compiler sees fpreset_arm64.c through the patched
    src/libs/mingw.zig manifest. The official binary embeds the unpatched
    manifest, so merely overlaying that new file leaves it invisible. For the
    seed only, append the exact patched payload to generic crt_handler.c under
    an ARM64 guard. A fresh-cache pthread link later proves the carrier is
    actually compiled; this is not wrapper or link-command injection.
    """
    embedded_paths = (b"crt/crt_handler.c", br"crt\crt_handler.c")
    if not any(binary_contains(zig, path) for path in embedded_paths):
        raise RuntimeError(
            "official seed does not advertise crt/crt_handler.c in its embedded "
            "MinGW source manifest"
        )

    crt_dir = zig_lib / "libc" / "mingw" / "crt"
    carrier = crt_dir / "crt_handler.c"
    payload = crt_dir / "fpreset_arm64.c"
    carrier_text = carrier.read_text(encoding="utf-8")
    if SEED_FPRESET_MARKER in carrier_text:
        raise RuntimeError(f"native seed fpreset carrier was already modified: {carrier}")
    payload_text = payload.read_text(encoding="utf-8")
    if "void _fpreset(void)" not in payload_text or "void fpreset(void)" not in payload_text:
        raise RuntimeError(f"unexpected patched ARM64 fpreset payload: {payload}")

    carrier.write_text(
        carrier_text
        + f"\n/* {SEED_FPRESET_MARKER}: official seed manifest compatibility. */\n"
        + "#if defined(__aarch64__)\n"
        + payload_text.rstrip()
        + "\n#endif\n",
        encoding="utf-8",
    )


def definition_dll(path: Path, stem: str) -> str:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r'^\s*LIBRARY\s+"?([^"\s]+)', line)
        if match:
            return match.group(1)
    return f"{stem}.dll"


def generate_implib(zig: Path, definition: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        zig,
        "dlltool",
        "-m",
        DLLTOOL_MACHINE,
        "-D",
        definition_dll(definition, output.stem.removeprefix("lib")),
        "-d",
        definition,
        "-l",
        output,
        capture_output=True,
        quiet=True,
    )
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"zig dlltool did not create a usable archive: {output}")


def preprocess_definition(
    zig: Path,
    source: Path,
    output: Path,
    include_dirs: tuple[Path, ...],
) -> None:
    command: list[Path | str] = [
        zig,
        "cc",
        "-E",
        "-P",
        "-target",
        WINDOWS_TARGET,
        "-x",
        "assembler-with-cpp",
    ]
    for include_dir in include_dirs:
        command.extend(("-I", include_dir))
    command.append(source)
    result = run(*command, capture_output=True, quiet=True)
    output.write_text(result.stdout, encoding="utf-8")
    if "EXPORTS" not in result.stdout:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"preprocessed definition has no EXPORTS section: {source}")


def archive_objects(zig: Path, output: Path, *objects: Path) -> None:
    run(zig, "ar", "rcs", output, *objects, capture_output=True, quiet=True)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"archiver did not create a usable archive: {output}")


def generate_import_libraries(
    zig: Path,
    zig_lib: Path,
    recipe_dir: Path,
) -> Path:
    mingw_root = zig_lib / "libc" / "mingw"
    lib_common = mingw_root / "lib-common"
    def_include = mingw_root / "def-include"
    libsrc = mingw_root / "libsrc"
    arm64_lib = mingw_root / "libarm64"
    supplemental = recipe_dir / "building" / "mingw-defs"
    arm64_lib.mkdir(parents=True, exist_ok=True)

    (lib_common / "synchronization.def").write_text(
        SYNCHRONIZATION_DEF, encoding="utf-8"
    )

    generated = 0
    for definition in sorted(lib_common.glob("*.def")):
        generate_implib(
            zig, definition, arm64_lib / f"lib{definition.stem}.a"
        )
        generated += 1

    for template in sorted(lib_common.glob("*.def.in")):
        stem = template.name.removesuffix(".def.in")
        if stem in SKIP_DEF_TEMPLATES:
            continue
        definition = arm64_lib / f"{stem}.def"
        preprocess_definition(zig, template, definition, (def_include,))
        generate_implib(zig, definition, arm64_lib / f"lib{stem}.a")
        generated += 1

    for template in sorted(supplemental.glob("*.def.in")):
        stem = template.name.removesuffix(".def.in")
        if stem in SKIP_DEF_TEMPLATES:
            continue
        output = arm64_lib / f"lib{stem}.a"
        if output.exists():
            continue
        definition = arm64_lib / f"{stem}.def"
        preprocess_definition(
            zig,
            template,
            definition,
            (supplemental, def_include, lib_common),
        )
        generate_implib(zig, definition, output)
        generated += 1

    for definition in sorted(supplemental.glob("*.def")):
        output = arm64_lib / f"lib{definition.stem}.a"
        if output.exists():
            continue
        generate_implib(zig, definition, output)
        generated += 1

    uuid_source = libsrc / "uuid.c"
    uuid_object = arm64_lib / "_uuid.o"
    uuid_archive = arm64_lib / "libuuid.a"
    run(
        zig,
        "cc",
        "-target",
        WINDOWS_TARGET,
        "-c",
        uuid_source,
        "-o",
        uuid_object,
    )
    archive_objects(zig, uuid_archive, uuid_object)
    uuid_object.unlink()
    generated += 1

    missing = sorted(
        name
        for name in REQUIRED_IMPORT_LIBS
        if not (arm64_lib / name).is_file()
        or (arm64_lib / name).stat().st_size == 0
    )
    if missing:
        raise RuntimeError(f"required ARM64 MinGW import libraries missing: {missing}")
    print(f"Generated {generated} native ARM64 MinGW import libraries")
    return arm64_lib


def crt_compile_args(zig_lib: Path) -> list[Path | str]:
    mingw_root = zig_lib / "libc" / "mingw"
    return [
        "-target",
        WINDOWS_TARGET,
        "-mcpu=baseline",
        "-c",
        "-std=gnu11",
        "-D__USE_MINGW_ANSI_STDIO=0",
        "-D__MSVCRT_VERSION__=0x700",
        "-D_CRTBLD",
        "-D_SYSCRT=1",
        "-D_WIN32_WINNT=0x0f00",
        "-DCRTDLL=1",
        "-DHAVE_CONFIG_H",
        "-isystem",
        zig_lib / "libc" / "include" / "any-windows-any",
        "-I",
        mingw_root / "include",
    ]


def generate_startup_objects(zig: Path, zig_lib: Path, arm64_lib: Path) -> None:
    crt_dir = zig_lib / "libc" / "mingw" / "crt"
    compile_args = crt_compile_args(zig_lib)
    sources = {
        "crt2.o": (crt_dir / "crtexe.c", ()),
        "crt2win.o": (crt_dir / "crtexewin.c", ("-D_WINDOWS",)),
        "dllcrt2.o": (crt_dir / "crtdll.c", ()),
    }
    for output_name, (source, extra_args) in sources.items():
        run(
            zig,
            "cc",
            *compile_args,
            *extra_args,
            source,
            "-o",
            arm64_lib / output_name,
        )


def generate_stub_archives(zig: Path, arm64_lib: Path) -> None:
    for library in ("gcc", "gcc_eh", "stdc++", "ssp"):
        identifier = library.replace("+", "_")
        source = arm64_lib / f".zig_{identifier}_stub.c"
        obj = source.with_suffix(".o")
        output = arm64_lib / f"lib{library}.a"
        source.write_text(
            f"int __zig_{identifier}_stub __attribute__((weak)) = 0;\n",
            encoding="utf-8",
        )
        run(zig, "cc", "-target", WINDOWS_TARGET, "-c", source, "-o", obj)
        archive_objects(zig, output, obj)
        source.unlink()
        obj.unlink()


def stage_runtime_archives(
    zig: Path,
    arm64_lib: Path,
) -> None:
    with tempfile.TemporaryDirectory(prefix="zig-win-arm64-runtime-") as tmp:
        work = Path(tmp)
        cache = work / "cache"
        source = work / "warm.c"
        exe = work / "warm.exe"
        source.write_text(
            "#include <stdio.h>\n"
            "#include <pthread.h>\n"
            "int main(void) { char b[8]; (void)snprintf(b, 8, \"%d\", 0); "
            "pthread_t t = pthread_self(); (void)t; return 0; }\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["ZIG_GLOBAL_CACHE_DIR"] = str(cache / "global")
        env["ZIG_LOCAL_CACHE_DIR"] = str(cache / "local")

        # This is deliberately a fresh-cache, no-injection link. The patched
        # _fpreset definition lives in crt_handler.c, a source the official
        # compiler already enumerates. pthread_self pulls in the
        # winpthreads path that historically exposed the missing symbol. Run
        # the result too: the native seed is accepted only if an ordinary user
        # command can compile, link, and execute it without private build glue.
        run(
            zig,
            "cc",
            "-target",
            WINDOWS_TARGET,
            "-pthread",
            source,
            "-o",
            exe,
            env=env,
        )
        assert_arm64_pe(exe)
        run(exe)

        candidates = [
            path
            for path in cache.rglob("libmingw32.lib")
            if path.stat().st_size > 1_000_000
        ]
        if not candidates:
            raise RuntimeError("native Zig cache did not materialize libmingw32.lib")
        runtime = max(candidates, key=lambda path: path.stat().st_size)

        listing = run(zig, "ar", "t", runtime, capture_output=True)
        if "crt_handler" not in listing.stdout:
            raise RuntimeError("crt_handler object is absent from libmingw32.lib")

        for name in RUNTIME_ARCHIVE_NAMES:
            shutil.copy2(runtime, arm64_lib / f"{name}.lib")
            shutil.copy2(runtime, arm64_lib / f"{name}.a")

    for name in RUNTIME_ARCHIVE_NAMES:
        for suffix in (".lib", ".a"):
            archive = arm64_lib / f"{name}{suffix}"
            if archive.stat().st_size < 1_000_000:
                raise RuntimeError(f"truncated native ARM64 runtime archive: {archive}")


def validate_toolchain(zig: Path, version: str, arm64_lib: Path) -> None:
    result = run(zig, "version", capture_output=True)
    if result.stdout.strip() != version:
        raise RuntimeError(f"expected Zig {version}, got {result.stdout.strip()!r}")

    for crt_object in CRT_OBJECTS:
        path = arm64_lib / crt_object
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"missing native ARM64 CRT startup object: {path}")

    with tempfile.TemporaryDirectory(prefix="zig-win-arm64-smoke-") as tmp:
        work = Path(tmp)
        env = os.environ.copy()
        env["ZIG_GLOBAL_CACHE_DIR"] = str(work / "cache")
        c_source = work / "smoke.c"
        cpp_source = work / "smoke.cpp"
        fpreset_source = work / "fpreset.c"
        obj = work / "smoke.obj"
        c_exe = work / "smoke.exe"
        cpp_exe = work / "smoke-cpp.exe"
        fpreset_exe = work / "smoke-fpreset.exe"
        msvc_archive = work / "smoke.lib"
        gnu_archive = work / "libsmoke.a"
        c_source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
        cpp_source.write_text(
            "#include <string>\n"
            "int main() { return std::string(\"arm64\").size() == 5 ? 0 : 1; }\n",
            encoding="utf-8",
        )
        fpreset_source.write_text(
            "extern void _fpreset(void);\n"
            "int main(void) { _fpreset(); return 0; }\n",
            encoding="utf-8",
        )

        run(
            zig,
            "cc",
            "-target",
            WINDOWS_TARGET,
            "-c",
            c_source,
            "-o",
            obj,
            env=env,
        )
        run(
            zig,
            "cc",
            "-target",
            WINDOWS_TARGET,
            c_source,
            "-o",
            c_exe,
            env=env,
        )
        run(
            zig,
            "c++",
            "-target",
            WINDOWS_TARGET,
            cpp_source,
            "-o",
            cpp_exe,
            env=env,
        )
        run(
            zig,
            "cc",
            "-target",
            WINDOWS_TARGET,
            fpreset_source,
            f"-L{arm64_lib}",
            "-lmingw32",
            "-o",
            fpreset_exe,
            env=env,
        )
        for exe in (c_exe, cpp_exe, fpreset_exe):
            assert_arm64_pe(exe)
            run(exe)

        run(zig, "lib", "/machine:arm64", f"/out:{msvc_archive}", obj)
        run(zig, "ar", "rcs", gnu_archive, obj)
        run(zig, "ranlib", gnu_archive)
        for archive in (msvc_archive, gnu_archive):
            if archive.read_bytes()[:8] != b"!<arch>\n":
                raise RuntimeError(f"invalid archive produced by Zig: {archive}")


def main() -> None:
    if os.environ.get("target_platform") != "win-arm64":
        raise RuntimeError("native seed installer is restricted to target_platform=win-arm64")
    if sys.platform != "win32" or platform.machine().lower() not in {"arm64", "aarch64"}:
        raise RuntimeError("native seed installer requires a native Windows ARM64 process")
    assert_arm64_pe(Path(sys.executable))

    src_dir = Path(os.environ["SRC_DIR"])
    recipe_dir = Path(os.environ["RECIPE_DIR"])
    prefix = Path(os.environ["PREFIX"])
    version = os.environ["NATIVE_SEED_VERSION"]
    if version != "0.17.0-dev.2033+af24fd11a":
        raise RuntimeError("native seed manifest compatibility must be reviewed for each Zig version")
    conda_triplet = os.environ["CONDA_TRIPLET"]
    seed_root = find_seed_root(src_dir)
    source_zig = seed_root / "zig.exe"
    assert_arm64_pe(source_zig)
    actual_version = run(source_zig, "version", capture_output=True).stdout.strip()
    if actual_version != version:
        raise RuntimeError(f"expected seed {version}, got {actual_version!r}")

    bin_dir = prefix / "Library" / "bin"
    zig_lib = prefix / "Library" / "lib" / "zig"
    doc_dir = prefix / "Library" / "doc"
    bin_dir.mkdir(parents=True, exist_ok=True)
    doc_dir.mkdir(parents=True, exist_ok=True)

    installed_zig = bin_dir / f"{conda_triplet}-zig.exe"
    shutil.copy2(source_zig, installed_zig)
    shutil.copytree(seed_root / "lib", zig_lib, dirs_exist_ok=True)
    # Runtime-source patches are useful to the official compiler because Zig
    # builds MinGW lazily from its installed library tree. Source-code patches
    # to the compiler itself still require the later native source rebuild.
    shutil.copytree(src_dir / "zig-source" / "lib", zig_lib, dirs_exist_ok=True)
    shutil.copy2(seed_root / "doc" / "langref.html", doc_dir / "langref.html")

    assert_arm64_pe(installed_zig)
    graft_seed_fpreset(installed_zig, zig_lib)
    arm64_lib = generate_import_libraries(installed_zig, zig_lib, recipe_dir)
    generate_startup_objects(installed_zig, zig_lib, arm64_lib)
    generate_stub_archives(installed_zig, arm64_lib)
    stage_runtime_archives(installed_zig, arm64_lib)
    validate_toolchain(installed_zig, version, arm64_lib)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"native Windows ARM64 Zig seed validation failed: {exc}", file=sys.stderr)
        raise
