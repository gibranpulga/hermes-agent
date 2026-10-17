"""Live revision evidence must match, independently of process liveness."""
from contextlib import contextmanager
import io
import json
from types import SimpleNamespace

import pytest

from scripts.agentdesk import native_lifecycle


@pytest.mark.parametrize('commit,healthy', [('a' * 40, True), ('b' * 40, False), (None, False)])
def test_live_native_revision_is_required(monkeypatch, capsys, commit, healthy):
    @contextmanager
    def response(url, timeout):
        yield io.StringIO(json.dumps({'ok': True, 'commit': commit} if '/api/' in url else {'status': 'ok'}))

    monkeypatch.setattr(native_lifecycle.request, 'urlopen', response)
    clock = iter([0, 0, 301])
    monkeypatch.setattr(native_lifecycle.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(native_lifecycle.time, 'sleep', lambda _: None)
    children = [SimpleNamespace(poll=lambda: None)]
    if healthy:
        native_lifecycle.ready('a' * 40, children)
    else:
        with pytest.raises(RuntimeError, match='revision or health'):
            native_lifecycle.ready('a' * 40, children)
        evidence = json.loads(capsys.readouterr().out)['nativeHealthEvidence']
        assert evidence['desktop']['commit'] == commit
        assert evidence['gateway'] == {'status': 'ok'}
