"""Exercise the shared Windows spawn helper against a real native child."""
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

slash = chr(92)
cases = ["", "plain", "two words", "tab" + chr(9) + "value", 'a "quoted" value',
         "C:" + slash + "Program Files" + slash,
         "backslash" + slash + '" and space',
         "two" + slash * 2 + '" and space', "trailing space " + slash, "&|<>^%"]
with tempfile.TemporaryDirectory(prefix="zig spawn roundtrip ") as temp:
    root = Path(temp)
    source = root / "spawn.c"
    rows = ",\n".join(json.dumps(value) for value in cases)
    source.write_text(r'''#include <stdio.h>
#include "nonunix_spawn.h"
int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--child") == 0) {
        for (int i = 2; i < argc; i++) {
            printf("%zu:", strlen(argv[i]));
            for (const unsigned char *p = (const unsigned char *)argv[i]; *p; p++)
                printf("%02x", *p);
            putchar('\n');
        }
        return 23;
    }
    const char *child[] = {argv[0], "--child",
''' + rows + ''', NULL};
    return zig_spawn_wait(argv[0], child);
}
''')
    executable = root / "spawn roundtrip.exe"
    subprocess.run([sys.argv[1], "cc", "-target", "aarch64-windows-gnu",
                    "-mcpu=baseline", "-Wall", "-Wextra", "-Werror",
                    "-I" + str(Path("recipe/building").resolve()),
                    str(source), "-o", str(executable)], check=True)
    data = executable.read_bytes()
    offset = struct.unpack_from("<I", data, 60)[0]
    assert data[:2] == b"MZ" and data[offset:offset + 4] == b"PE\0\0"
    assert struct.unpack_from("<H", data, offset + 4)[0] == 0xAA64
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 23, (result.returncode, result.stderr)
    expected = [f"{len(value)}:{value.encode().hex()}" for value in cases]
    assert result.stdout.splitlines() == expected, (result.stdout, expected)
print("PASS: native ARM64 child received all 10 arguments exactly and returned exit code 23")
