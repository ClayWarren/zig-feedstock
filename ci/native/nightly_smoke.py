"""Fork-only ordinary C smoke for a checksum-pinned official development seed."""

import json
import platform
import sys
import urllib.request
import zipfile
from pathlib import Path

from accept import check_pe
from compare_seeds import EVIDENCE, digest, smoke

VERSION = '0.17.0-dev.2033+af24fd11a'
URL = f'https://ziglang.org/builds/zig-aarch64-windows-{VERSION}.zip'
SHA256 = 'f63987d4f4e90e9b5aff9e38d4265696d9abd83de2e70d4bb9b4ac22d6febf2c'


def main():
    if sys.platform != 'win32' or platform.machine().lower() not in {'arm64', 'aarch64'}:
        raise RuntimeError('native Windows ARM64 Python required')
    check_pe(sys.executable)
    EVIDENCE.mkdir(exist_ok=True)
    records = [json.loads(path.read_text()) for path in (Path(sys.prefix) / 'conda-meta').glob('*.json')]
    if not records or any(record['subdir'] not in {'win-arm64', 'noarch'} for record in records):
        raise RuntimeError('unexpected environment architecture')
    (EVIDENCE / 'installed-records.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    work = Path('C:/nightly-seed')
    work.mkdir(exist_ok=False)
    archive = work / 'seed.zip'
    with urllib.request.urlopen(URL, timeout=120) as response, archive.open('wb') as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    actual = digest(archive)
    provenance = {'version': VERSION, 'url': URL, 'expected_sha256': SHA256, 'actual_sha256': actual}
    (EVIDENCE / 'provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    if actual != SHA256:
        raise RuntimeError('official archive checksum mismatch')
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(work)
    root = work / f'zig-aarch64-windows-{VERSION}'
    (EVIDENCE / 'smoke.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
    lane = smoke('nightly', root / 'zig.exe', root / 'lib')
    lane['reported_version'] = (EVIDENCE / 'nightly-version.log').read_text().strip()
    report = {'machine': platform.machine(), 'provenance': provenance, 'lane': lane}
    (EVIDENCE / 'nightly.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
    return int(lane['reported_version'] != VERSION or any(
        lane.get(step, {}).get('returncode') != 0 for step in ('version', 'object', 'link', 'run')
    ))


if __name__ == '__main__':
    sys.exit(main())
