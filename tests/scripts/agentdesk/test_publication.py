"""Release ordering is independent of workflow queue completion order."""
import pytest

from scripts.agentdesk.publication import eligible, required_validation


def test_superseded_commit_cannot_publish_even_when_its_tests_pass():
    assert not eligible('old', 'new', 10, [])


def test_older_slow_completion_cannot_replace_newer_publication():
    assert not eligible('main', 'main', 10, [9, 11])
    assert eligible('main', 'main', 12, [9, 11])


def test_rerun_cannot_replace_published_artifacts():
    assert not eligible('main', 'main', 12, [12])


def test_missing_failed_or_cancelled_validation_never_creates_a_release_record():
    checks = dict.fromkeys(['install', 'restart', 'runningRevision', 'health', 'recovery'], True)
    assert required_validation(checks, checks, {'regression': True})['regression'] is True
    for field in checks:
        with pytest.raises(RuntimeError):
            required_validation({**checks, field: False}, checks, {'regression': True})
        with pytest.raises(RuntimeError):
            required_validation(checks, {**checks, field: False}, {'regression': True})
    for regression in ({}, {'regression': False}):
        with pytest.raises(RuntimeError):
            required_validation(checks, checks, regression)


def test_archive_parts_preserve_the_exact_tested_bytes(tmp_path, monkeypatch):
    import hashlib
    from scripts.agentdesk import publication

    monkeypatch.setattr(publication, 'PART_BYTES', 3)
    archive = tmp_path / 'hermes-native.tar.gz'
    payload = b'abcdefgh'
    archive.write_bytes(payload)
    record = publication.archive_record(archive)
    assert not archive.exists()
    assert record['sha256'] == hashlib.sha256(payload).hexdigest()
    assert len(record['parts']) == 3
    rebuilt = b''
    for part in record['parts']:
        content = (tmp_path / part['filename']).read_bytes()
        assert len(content) == part['size']
        assert hashlib.sha256(content).hexdigest() == part['sha256']
        rebuilt += content
    assert rebuilt == payload


def test_main_moving_during_upload_prevents_final_publication(tmp_path, monkeypatch):
    import json
    from scripts.agentdesk import publication

    monkeypatch.chdir(tmp_path)
    (tmp_path / 'release').mkdir()
    record = {'revision': 'a' * 40, 'sequence': 5, 'version': 'test', 'notes': 'test',
              'deployments': {'image': {'filename': 'hermes-image.tar.gz'}}}
    (tmp_path / 'release/release.json').write_text(json.dumps(record))
    operations = []
    def output(*arguments, **kwargs):
        operations.append((arguments, kwargs))
        if 'git/ref/heads/main' in ' '.join(arguments):
            return record['revision'] if len(operations) == 1 else 'b' * 40
        if '--slurp' in arguments:
            return '[[]]'
        return ''
    monkeypatch.setattr(publication, 'output', output)
    with pytest.raises(RuntimeError, match='superseded'):
        publication.publish()
    upload = [operation for operation in operations if operation[0][:3] == ('gh', 'release', 'create')]
    assert upload[0][1]['timeout'] == 1800
    assert not any(operation[0][:3] == ('gh', 'release', 'edit') for operation in operations)


def test_image_identity_comes_from_the_tested_archive_not_a_daemon_local_id(tmp_path):
    import hashlib
    import io
    import json
    import tarfile
    from scripts.agentdesk.publication import archive_image_id

    revision = 'a' * 40
    config = {'os': 'linux', 'architecture': 'amd64', 'config': {'Labels': {'org.opencontainers.image.revision': revision}}}
    data = json.dumps(config).encode()
    checksum = hashlib.sha256(data).hexdigest()
    path = tmp_path / 'image.tar.gz'
    name = 'blobs/sha256/' + checksum
    with tarfile.open(path, 'w:gz') as bundle:
        for filename, content in [(name, data), ('manifest.json', json.dumps([{'Config': name, 'RepoTags': ['hermes-release:candidate']}]).encode())]:
            member = tarfile.TarInfo(filename)
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))
    assert archive_image_id(path, revision) == 'sha256:' + checksum
    with pytest.raises(RuntimeError, match='source identity'):
        archive_image_id(path, 'b' * 40)


def test_release_label_uses_canonical_version_and_rejects_placeholder_or_stale_identity(monkeypatch):
    from types import SimpleNamespace
    from scripts.agentdesk import publication

    revision = 'a' * 40
    monkeypatch.setattr(publication, 'get_version_info', lambda: SimpleNamespace(commit=revision, base_version='0.21.6'))
    assert publication.release_version(revision, 10) == '0.21.6+fork.10'
    for base, commit in [('0.0.0', revision), ('unknown', revision), ('0.21.6', 'b' * 40)]:
        monkeypatch.setattr(publication, 'get_version_info', lambda: SimpleNamespace(commit=commit, base_version=base))
        with pytest.raises(RuntimeError, match='version or source identity'):
            publication.release_version(revision, 10)


def test_registry_records_bind_manifest_and_config_to_allowed_repository(monkeypatch):
    from scripts.agentdesk import publication

    allowed = 'docker.io/test-owner/hermes-agent'
    monkeypatch.setenv('HERMES_IMAGE_REPOSITORY', allowed)
    value = publication.registry_record(allowed, 'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64)
    assert value['sha256'] == 'a' * 64
    assert value['platform'] == 'linux/amd64'
    for repository, digest in [('docker.io/attacker/image', value['digest']), (allowed, 'latest')]:
        with pytest.raises(RuntimeError, match='identity'):
            publication.registry_record(repository, digest, value['imageId'])
    monkeypatch.delenv('HERMES_IMAGE_REPOSITORY')
    with pytest.raises(RuntimeError, match='identity'):
        publication.registry_record(allowed, value['digest'], value['imageId'])


def test_registry_images_are_not_uploaded_as_redundant_release_archives():
    from scripts.agentdesk import publication

    record = {'deployments': {'image': {'transport': 'registry'},
                              'native': {'filename': 'hermes-native.tar.gz'}}}
    assert publication.artifact_assets(record) == [str(publication.RELEASE_DIR / 'hermes-native.tar.gz')]


def test_validator_pulls_the_published_digest_and_rejects_changed_config(tmp_path, monkeypatch):
    import json
    from scripts.agentdesk import publication

    allowed = 'docker.io/test-owner/hermes-agent'
    monkeypatch.setenv('HERMES_IMAGE_REPOSITORY', allowed)
    monkeypatch.setenv('GITHUB_SHA', 'c' * 40)
    monkeypatch.setattr(publication, 'RELEASE_DIR', tmp_path)
    value = publication.registry_record(allowed, 'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64)
    (tmp_path / 'image.json').write_text(json.dumps(value))
    commands = []
    def output(*arguments, **kwargs):
        commands.append(arguments)
        return json.dumps([{'Id': 'sha256:' + 'f' * 64}]) if 'inspect' in arguments else ''
    monkeypatch.setattr(publication, 'output', output)
    with pytest.raises(RuntimeError, match='differs'):
        publication.pull_image()
    assert commands[0] == ('docker', 'pull', '--platform', 'linux/amd64', allowed + '@' + value['digest'])


def test_record_keeps_the_version_validated_before_later_tag_changes(monkeypatch):
    from scripts.agentdesk import publication

    def moved_tags():
        raise AssertionError('Publication must not re-resolve mutable release tags')

    monkeypatch.setattr(publication, 'get_version_info', moved_tags)
    assert publication.release_version('a' * 40, 13, '0.21.5+fork.13') == '0.21.5+fork.13'
    for version in ['0.21.5+fork.12', '0.0.0+fork.13', 'unknown', '0.21.5+fork.13\n']:
        with pytest.raises(RuntimeError, match='validated build sequence'):
            publication.release_version('a' * 40, 13, version)
