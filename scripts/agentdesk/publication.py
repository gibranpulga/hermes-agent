"""Freeze a release record and refuse out-of-order publication."""
import hashlib
import json
import os
import platform
import re
import tarfile
from pathlib import Path
import subprocess
from hermes_cli.version_info import get_version_info

REPOSITORY = 'gibranpulga/hermes-agent'
PART_BYTES = 1024**3
RELEASE_DIR = Path(os.environ.get('AGENTDESK_RELEASE_DIR', 'release'))


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


def output(*args, timeout=60):
    return subprocess.check_output(args, text=True, encoding="utf-8", timeout=timeout).strip()


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
        if artifact.get('transport') == 'registry':
            continue
        names.extend(part['filename'] for part in artifact['parts']) if artifact.get('parts') else names.append(artifact['filename'])
    return [str(RELEASE_DIR / name) for name in names]



def archive_image_id(path, revision):
    """Publish a config-blob digest, independent of the builder's image store."""
    manifest = None
    with tarfile.open(path, 'r|*') as bundle:
        for member in bundle:
            if member.name == 'manifest.json':
                if not member.isfile() or member.size > 1024**2:
                    raise RuntimeError('Image archive manifest is invalid')
                manifest = json.loads(bundle.extractfile(member).read())
    if not isinstance(manifest, list) or len(manifest) != 1 or manifest[0].get('RepoTags') != ['hermes-release:candidate']:
        raise RuntimeError('Image archive must contain the fixed candidate only')
    name = manifest[0].get('Config', '')
    match = re.fullmatch(r'(?:blobs/sha256/([a-f0-9]{64})|([a-f0-9]{64})\.json)', name)
    if not match:
        raise RuntimeError('Image configuration name is not a content digest')
    checksum = match.group(1) or match.group(2)
    with tarfile.open(path, 'r|*') as bundle:
        for member in bundle:
            if member.name != name:
                continue
            if not member.isfile() or member.size > 1024**2:
                raise RuntimeError('Image configuration is invalid')
            content = bundle.extractfile(member).read()
            config = json.loads(content)
            if hashlib.sha256(content).hexdigest() != checksum:
                raise RuntimeError('Image configuration digest changed')
            if (config.get('os') != 'linux' or config.get('architecture') != 'amd64' or
                    config.get('config', {}).get('Labels', {}).get('org.opencontainers.image.revision') != revision):
                raise RuntimeError('Image platform or source identity changed')
            return 'sha256:' + checksum
    raise RuntimeError('Image configuration is missing from the tested archive')


def registry_record(repository, digest, image_id):
    allowed = os.environ.get('HERMES_IMAGE_REPOSITORY', '')
    if (not re.fullmatch(r'docker\.io/[a-z0-9_-]+/[a-z0-9_-]+', allowed) or repository != allowed or
            not re.fullmatch('sha256:[a-f0-9]{64}', digest) or
            not re.fullmatch('sha256:[a-f0-9]{64}', image_id)):
        raise RuntimeError('Registry identity is invalid')
    return {'transport': 'registry', 'repository': repository, 'digest': digest,
            'sha256': digest.removeprefix('sha256:'), 'imageId': image_id, 'platform': 'linux/amd64'}


def push_image():
    """Runs on a fresh publisher daemon; never executes candidate code."""
    archive = RELEASE_DIR / 'hermes-image.tar.gz'
    config_id = archive_image_id(archive, os.environ['GITHUB_SHA'])
    repository = os.environ['HERMES_IMAGE_REPOSITORY']
    registry_record(repository, 'sha256:' + '0' * 64, config_id)
    output('docker', 'load', '--input', str(archive), timeout=900)
    tag = repository + ':candidate-' + os.environ['GITHUB_SHA'] + '-' + os.environ['GITHUB_RUN_ID']
    output('docker', 'tag', 'hermes-release:candidate', tag)
    output('docker', 'push', tag, timeout=900)
    item = json.loads(output('docker', 'image', 'inspect', tag))[0]
    refs = [ref for ref in item.get('RepoDigests', []) if ref.removeprefix('docker.io/').startswith(repository.removeprefix('docker.io/') + '@')]
    if len(refs) != 1:
        raise RuntimeError('Published manifest digest is ambiguous')
    value = registry_record(repository, refs[0].split('@')[1], config_id)
    (RELEASE_DIR / 'image.json').write_text(json.dumps(value, sort_keys=True) + '\n', encoding='utf-8')


def pull_image():
    image = json.loads((RELEASE_DIR / 'image.json').read_text(encoding='utf-8-sig'))
    if image != registry_record(image['repository'], image['digest'], image['imageId']):
        raise RuntimeError('Registry record changed')
    reference = image['repository'] + '@' + image['digest']
    output('docker', 'pull', '--platform', 'linux/amd64', reference, timeout=900)
    item = json.loads(output('docker', 'image', 'inspect', reference))[0]
    if (item['Id'] != image['imageId'] or item['Os'] != 'linux' or item['Architecture'] != 'amd64' or
            reference.removeprefix('docker.io/') not in [ref.removeprefix('docker.io/') for ref in item.get('RepoDigests', [])] or
            (item['Config'].get('Labels') or {}).get('org.opencontainers.image.revision') != os.environ['GITHUB_SHA']):
        raise RuntimeError('Pulled image differs from built candidate')


def release_version(revision, sequence, frozen=None):
    if frozen is not None:
        if frozen.startswith('0.0.0+fork.') or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?\+fork\.' + re.escape(str(sequence)), frozen):
            raise RuntimeError('Frozen release version does not match the validated build sequence')
        return frozen
    info = get_version_info()
    if info.commit != revision or info.base_version in ('unknown', '0.0.0'):
        raise RuntimeError('Canonical release version or source identity is unavailable')
    return info.base_version + '+fork.' + str(sequence)


def record():
    revision = os.environ['GITHUB_SHA']
    if output('git', 'rev-parse', 'HEAD') != revision:
        raise RuntimeError('Build checkout moved')
    checks = required_validation(
        json.loads((RELEASE_DIR / 'lifecycle.json').read_text(encoding='utf-8-sig')),
        json.loads((RELEASE_DIR / 'native-lifecycle.json').read_text(encoding='utf-8-sig')),
        json.loads((RELEASE_DIR / 'regression.json').read_text(encoding='utf-8-sig')),
    )
    image = json.loads((RELEASE_DIR / 'image.json').read_text(encoding='utf-8-sig'))
    if image != registry_record(image['repository'], image['digest'], image['imageId']):
        raise RuntimeError('Registry record changed')
    native = archive_record(RELEASE_DIR / 'hermes-native.tar.gz')
    native['minimumGlibc'] = platform.libc_ver()[1]
    value = {'schema': 1, 'repository': REPOSITORY,
             'workflow': REPOSITORY + '/.github/workflows/agentdesk-release.yml',
             'revision': revision, 'version': release_version(revision, os.environ['GITHUB_RUN_NUMBER'], os.environ.get('AGENTDESK_RELEASE_VERSION')),
             'sequence': int(os.environ['GITHUB_RUN_NUMBER']), 'runId': os.environ['GITHUB_RUN_ID'],
             'notes': output('git', 'log', '-1', '--format=%s%n%b'),
             'compatibility': {'protocol': 1, 'os': 'linux', 'arch': 'x64', 'stateSchema': 1},
             'deployments': {'image': image, 'native': native}, 'validation': checks}
    (RELEASE_DIR / 'release.json').write_text(json.dumps(value, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def assert_publication_current(record):
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


def publish():
    record = json.loads((RELEASE_DIR / 'release.json').read_text(encoding="utf-8-sig"))
    assert_publication_current(record)
    tag = 'agentdesk-' + record['revision']
    # No clobber and no mutable release channel. Re-runs cannot replace artifacts.
    output('gh', 'release', 'create', tag, '--repo', REPOSITORY, '--target', record['revision'],
           '--draft', '--title', record['version'], '--notes', record['notes'],
           *[str(RELEASE_DIR / name) for name in ('release.json', 'release.json.sigstore', 'lifecycle.json', 'native-lifecycle.json')], *artifact_assets(record), timeout=1800)
    # Upload may take minutes. Fence again so a moved main or newer release
    # cannot be superseded while the tested bytes were being uploaded.
    assert_publication_current(record)
    # Flip is the final publication operation; failed builds never reach it.
    output('gh', 'release', 'edit', tag, '--repo', REPOSITORY, '--draft=false', '--latest=false')


if __name__ == '__main__':
    import sys
    {'record': record, 'publish': publish, 'push-image': push_image, 'pull-image': pull_image,
     'version': lambda: print(release_version(os.environ['GITHUB_SHA'], os.environ['GITHUB_RUN_NUMBER']))}[sys.argv[1]]()
