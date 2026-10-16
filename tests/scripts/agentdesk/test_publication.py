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
