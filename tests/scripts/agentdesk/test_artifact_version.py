"""Both tested deployment artifacts must report the published release version."""
import io
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from hermes_cli import version_info
from scripts.agentdesk import lifecycle, native_lifecycle
from scripts.write_install_stamp import build_stamp


def test_packaged_stamp_round_trips_the_exact_release_label(tmp_path, monkeypatch):
    version = '0.21.5+fork.13'
    stamp = build_stamp(commit='a' * 40, base_version=version, distance=0,
                        branch='main', dirty=False, commit_date=123,
                        source='ci', distribution='docker', update_mechanism='external')
    path = tmp_path / 'install-stamp.json'
    path.write_text(json.dumps(stamp))
    monkeypatch.setattr(version_info, '_resolve_stamp_file', lambda: path)
    info = version_info._stamp_version_info()
    assert info.base_version == info.display_version == version
    assert info.commit == 'a' * 40


@pytest.mark.parametrize('reported', ['0.21.5+fork.13', 'unknown'])
def test_image_health_requires_the_frozen_version_even_with_correct_revision(monkeypatch, reported):
    monkeypatch.setattr(lifecycle, 'run', lambda *args: json.dumps(
        {'ok': True, 'commit': 'a' * 40, 'version': reported} if '/api/health' in args[-1] else {'status': 'ok'}))
    if reported == 'unknown':
        with pytest.raises(RuntimeError, match='version'):
            lifecycle.probe('fixture', 'a' * 40, '0.21.5+fork.13')
    else:
        lifecycle.probe('fixture', 'a' * 40, '0.21.5+fork.13')


@pytest.mark.parametrize('reported', ['0.21.5+fork.13', 'unknown'])
def test_native_health_requires_the_frozen_version_even_with_correct_revision(monkeypatch, reported):
    @contextmanager
    def response(url, timeout):
        yield io.StringIO(json.dumps({'ok': True, 'commit': 'a' * 40, 'version': reported}
                                    if '/api/' in url else {'status': 'ok'}))

    monkeypatch.setattr(native_lifecycle.request, 'urlopen', response)
    clock = iter([0, 0, 301])
    monkeypatch.setattr(native_lifecycle.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(native_lifecycle.time, 'sleep', lambda _: None)
    children = [SimpleNamespace(poll=lambda: None)]
    if reported == 'unknown':
        with pytest.raises(RuntimeError, match='revision or health'):
            native_lifecycle.ready('a' * 40, children, '0.21.5+fork.13')
    else:
        native_lifecycle.ready('a' * 40, children, '0.21.5+fork.13')
