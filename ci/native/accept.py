"""Fork-only native acceptance; no publishing and no skipped recipe tests."""

import json
import platform
import struct
import subprocess
import sys
from pathlib import Path


def check_pe(path):
    with Path(path).open("rb") as stream:
        assert stream.read(2) == b"MZ", path
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        assert stream.read(4) == b"PE\0\0", path
        machine = struct.unpack("<H", stream.read(2))[0]
        assert machine == 0xAA64, (path, hex(machine))
    print("PASS native AA64:", path, flush=True)


def main():
    assert sys.platform == "win32"
    assert platform.machine().lower() in {"arm64", "aarch64"}
    check_pe(sys.executable)
    lane = sys.argv[1]
    records = [json.loads(path.read_text()) for path in (Path(sys.prefix) / "conda-meta").glob("*.json")]
    assert records
    for record in records:
        assert record["subdir"] in {"win-arm64", "noarch"}, record
        assert record["url"].startswith("https://conda.anaconda.org/conda-forge/"), record
    Path("evidence/installed-records.json").write_text(json.dumps(records, indent=2))
    if lane == "protobuf":
        import google.protobuf
        from google._upb import _message
        from google.protobuf import json_format, struct_pb2, timestamp_pb2
        from google.protobuf.internal import api_implementation

        record = next(record for record in records if record["name"] == "protobuf")
        assert (record["version"], record["build"]) == ("7.35.1", "py314hf717eb2_3")
        assert google.protobuf.__version__ == "7.35.1"
        assert api_implementation.Type() == "upb"
        check_pe(_message.__file__)
        original = struct_pb2.Struct()
        original.update({"platform": "win-arm64", "values": [1, 2, 3], "native": True})
        decoded = struct_pb2.Struct.FromString(original.SerializeToString())
        assert decoded == original
        assert json_format.Parse(json_format.MessageToJson(original), struct_pb2.Struct()) == original
        timestamp = timestamp_pb2.Timestamp(seconds=1788696000, nanos=123456789)
        assert timestamp_pb2.Timestamp.FromString(timestamp.SerializeToString()) == timestamp
        print("PASS public-channel Protobuf build 3: native upb, binary/JSON round trips, timestamp", flush=True)
    elif lane == "zig":
        subprocess.run([
            "rattler-build", "build", "--recipe", "recipe/recipe.yaml",
            "--target-platform", "win-arm64", "--build-platform", "win-arm64",
            "-m", "ci/native/win-arm64.yaml",
            # The rendered variant already sets channel_sources=conda-forge.
            "--no-config", "--output-dir", "C:/bld",
        ], check=True)
        packages = list(Path("C:/bld/win-arm64").glob("*.conda"))
        assert len(packages) == 4, packages
        print("PASS four native Zig outputs built and recipe tests completed", flush=True)
    else:
        raise ValueError(lane)


if __name__ == "__main__":
    main()
