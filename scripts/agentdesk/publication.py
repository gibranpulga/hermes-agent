"""Freeze a release record and refuse out-of-order publication."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tomllib

REPOSITORY = 'gibranpulga/hermes-agent'


def eligible(candidate, main, sequence, published):
    return candidate == main and sequence > max(published, default=0)


def output(*args):
    return subprocess.check_output(args, text=True, encoding="utf-8", timeout=60).strip()


def record():
    revision = os.environ['GITHUB_SHA']
    if output('git', 'rev-parse', 'HEAD') != revision:
        raise RuntimeError('Build checkout moved')
    checks = json.loads(Path('release/lifecycle.json').read_text(encoding="utf-8-sig"))
    if any(checks.get(key) is not True for key in ('install', 'restart', 'runningRevision', 'health', 'recovery')):
        raise RuntimeError('Lifecycle validation incomplete')
    checks['regression'] = True  # Invoked only after the blocking regression step.
    archive = Path('release/hermes-image.tar.gz')
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if archive.stat().st_size >= 2 * 1024**3:
        raise RuntimeError('Release asset exceeds GitHub distribution limit')
    native_checks = json.loads(Path('release/native-lifecycle.json').read_text(encoding='utf-8-sig'))
    if native_checks != {key: True for key in checks if key != 'regression'}:
        raise RuntimeError('Native lifecycle validation incomplete')
    native_archive = Path('release/hermes-native.tar.gz')
    if native_archive.stat().st_size >= 2 * 1024**3:
        raise RuntimeError('Native asset exceeds GitHub distribution limit')
    with native_archive.open('rb') as stream:
        native_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    value = {'schema': 1, 'repository': REPOSITORY,
             'workflow': REPOSITORY + '/.github/workflows/agentdesk-release.yml',
             'revision': revision, 'version': tomllib.loads(Path('pyproject.toml').read_text(encoding="utf-8-sig"))['project']['version'] + '+fork.' + os.environ['GITHUB_RUN_NUMBER'],
             'sequence': int(os.environ['GITHUB_RUN_NUMBER']), 'runId': os.environ['GITHUB_RUN_ID'],
             'notes': output('git', 'log', '-1', '--format=%s%n%b'),
             'compatibility': {'protocol': 1, 'os': 'linux', 'arch': 'x64', 'stateSchema': 1},
             'deployments': {'image': {'filename': archive.name, 'sha256': digest, 'imageId': Path('release/image-id').read_text(encoding="utf-8-sig").strip()}, 'native': {'filename': native_archive.name, 'sha256': native_digest}},
             'validation': checks}
    Path('release/release.json').write_text(json.dumps(value, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def publish():
    record = json.loads(Path('release/release.json').read_text(encoding="utf-8-sig"))
    main = output('gh', 'api', 'repos/' + REPOSITORY + '/git/ref/heads/main', '--jq', '.object.sha')
    releases = json.loads(output('gh', 'api', '--paginate', '--slurp', 'repos/' + REPOSITORY + '/releases?per_page=100'))
    published = []
    for page in releases:
        for item in page:
            if item['draft'] or not item['tag_name'].startswith('agentdesk-'):
                continue
            # Publication authority is the fixed workflow; clients independently
            # verify its signed record. Names are only an ordering fence here.
            for asset in item['assets']:
                if asset['name'] == 'release.json':
                    data = output('gh', 'api', '-H', 'Accept: application/octet-stream',
                                  'repos/' + REPOSITORY + '/releases/assets/' + str(asset['id']))
                    published.append(json.loads(data)['sequence'])
    if not eligible(record['revision'], main, record['sequence'], published):
        raise RuntimeError('Candidate superseded or older than a published release')
    tag = 'agentdesk-' + record['revision']
    # No clobber and no mutable release channel. Re-runs cannot replace artifacts.
    output('gh', 'release', 'create', tag, '--repo', REPOSITORY, '--target', record['revision'],
           '--draft', '--title', record['version'], '--notes', record['notes'],
           'release/release.json', 'release/release.json.sigstore', 'release/hermes-image.tar.gz', 'release/hermes-native.tar.gz', 'release/lifecycle.json', 'release/native-lifecycle.json')
    # Flip is the final publication operation; failed builds never reach it.
    output('gh', 'release', 'edit', tag, '--repo', REPOSITORY, '--draft=false', '--latest=false')


if __name__ == '__main__':
    import sys
    {'record': record, 'publish': publish}[sys.argv[1]]()
