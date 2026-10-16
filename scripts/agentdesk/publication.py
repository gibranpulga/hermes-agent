"""Freeze a release record and refuse out-of-order publication."""
import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess
import tomllib

REPOSITORY = 'gibranpulga/hermes-agent'
PART_BYTES = 1024**3


def eligible(candidate, main, sequence, published):
    return candidate == main and sequence > max(published, default=0)


def required_validation(image, native, regression):
    keys = {'install', 'restart', 'runningRevision', 'health', 'recovery'}
    if regression != {'regression': True} or any(value is not True for value in regression.values()):
        raise RuntimeError('Regression validation incomplete')
    for checks in (image, native):
        if not isinstance(checks, dict) or set(checks) != keys or any(value is not True for value in checks.values()):
            raise RuntimeError('Lifecycle validation incomplete')
    return {**image, 'regression': True}


def output(*args):
    return subprocess.check_output(args, text=True, encoding="utf-8", timeout=60).strip()


def archive_record(path):
    size = path.stat().st_size
    if not 0 < size <= 8 * 1024**3:
        raise RuntimeError('Release archive exceeds supported installed-transfer size')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    value = {'filename': path.name, 'sha256': digest}
    if size <= PART_BYTES:
        return value
    parts = []
    with path.open('rb') as source:
        while source.tell() < size:
            part = path.with_name(path.name + '.part-' + str(len(parts)).zfill(3))
            checksum = hashlib.sha256()
            remaining = min(PART_BYTES, size - source.tell())
            count = remaining
            with part.open('wb') as output:
                while remaining:
                    data = source.read(min(1024**2, remaining))
                    if not data:
                        raise RuntimeError('Release archive changed while partitioning')
                    output.write(data)
                    checksum.update(data)
                    remaining -= len(data)
            parts.append({'filename': part.name, 'sha256': checksum.hexdigest(), 'size': count})
    value['parts'] = parts
    path.unlink()
    return value


def artifact_assets(record):
    names = []
    for artifact in record['deployments'].values():
        names.extend(part['filename'] for part in artifact['parts']) if artifact.get('parts') else names.append(artifact['filename'])
    return [str(Path('release') / name) for name in names]


def record():
    revision = os.environ['GITHUB_SHA']
    if output('git', 'rev-parse', 'HEAD') != revision:
        raise RuntimeError('Build checkout moved')
    checks = required_validation(
        json.loads(Path('release/lifecycle.json').read_text(encoding='utf-8-sig')),
        json.loads(Path('release/native-lifecycle.json').read_text(encoding='utf-8-sig')),
        json.loads(Path('release/regression.json').read_text(encoding='utf-8-sig')),
    )
    image = archive_record(Path('release/hermes-image.tar.gz'))
    image['imageId'] = Path('release/image-id').read_text(encoding='utf-8-sig').strip()
    native = archive_record(Path('release/hermes-native.tar.gz'))
    native['minimumGlibc'] = platform.libc_ver()[1]
    value = {'schema': 1, 'repository': REPOSITORY,
             'workflow': REPOSITORY + '/.github/workflows/agentdesk-release.yml',
             'revision': revision, 'version': tomllib.loads(Path('pyproject.toml').read_text(encoding='utf-8-sig'))['project']['version'] + '+fork.' + os.environ['GITHUB_RUN_NUMBER'],
             'sequence': int(os.environ['GITHUB_RUN_NUMBER']), 'runId': os.environ['GITHUB_RUN_ID'],
             'notes': output('git', 'log', '-1', '--format=%s%n%b'),
             'compatibility': {'protocol': 1, 'os': 'linux', 'arch': 'x64', 'stateSchema': 1},
             'deployments': {'image': image, 'native': native}, 'validation': checks}
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
           'release/release.json', 'release/release.json.sigstore', 'release/lifecycle.json', 'release/native-lifecycle.json', *artifact_assets(record))
    # Flip is the final publication operation; failed builds never reach it.
    output('gh', 'release', 'edit', tag, '--repo', REPOSITORY, '--draft=false', '--latest=false')


if __name__ == '__main__':
    import sys
    {'record': record, 'publish': publish}[sys.argv[1]]()
