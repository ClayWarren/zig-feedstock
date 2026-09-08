"""Fork-only recipe copy pinned to an exact retained x64 compiler artifact."""
import json
from pathlib import Path
import shutil

seed = Path('C:/seed')
repodata = json.loads((seed / 'win-64/repodata.json').read_text())
candidates = [(name, record) for name, record in repodata['packages.conda'].items()
              if record['name'] == 'zig_impl_win-64']
assert len(candidates) == 1, candidates
filename, record = candidates[0]
assert '2033_af24fd11a' in record['build'], record
print('SELECTED SEED:', filename, record['sha256'], flush=True)
destination = Path('C:/selected-recipe')
shutil.copytree('recipe', destination, ignore=shutil.ignore_patterns('__pycache__'))
recipe = destination / 'recipe.yaml'
content = recipe.read_text()
old = '  snapshot_ref: "1970"'
assert content.count(old) == 1
recipe.write_text(content.replace(old, '  snapshot_ref: "2033"'))
variant = Path('.ci_support/win_64_cross_target_platform_win-arm64.yaml').read_text()
old = '- conda-forge/label/zig_dev,conda-forge'
assert variant.count(old) == 1
Path('C:/selected-variant.yaml').write_text(
    variant.replace(old, '- file:///C:/seed,conda-forge/label/zig_dev,conda-forge'))

# Assert the selected record before building wrappers and before the unchanged
# entry-point test. The copied recipe is disposable; maintained inputs stay put.
attestation = '''
import json as seed_json
from pathlib import Path as SeedPath
seed_prefix = SeedPath(os.environ[SEED_PREFIX_ENV])
seed_records = [seed_json.loads(p.read_text()) for p in (seed_prefix / 'conda-meta').glob('zig_impl_win-64-*.json')]
assert len(seed_records) == 1, seed_records
seed_record = seed_records[0]
assert seed_record['build'] == SEED_BUILD, seed_record
assert seed_record['sha256'] == SEED_SHA256, seed_record
assert seed_record['url'].lower().startswith('file:///c:/seed/'), seed_record
print('ATTESTED LOCAL SEED:', seed_record['url'], seed_record['sha256'], flush=True)
'''.replace('SEED_BUILD', repr(record['build'])).replace('SEED_SHA256', repr(record['sha256']))
for relative, prefix_env in [('install_zig_activation.py', 'BUILD_PREFIX'),
                             ('testing/test_wl_entry_translation.py', 'PREFIX')]:
    path = destination / relative
    content = path.read_text()
    marker = '    main()'
    assert content.count(marker) == 1, relative
    check = attestation.replace('SEED_PREFIX_ENV', repr(prefix_env))
    path.write_text(content.replace(marker, '\n'.join('    ' + line for line in check.splitlines()) + '\n' + marker))
