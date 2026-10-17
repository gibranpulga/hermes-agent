"""Bounded runtime supervision must handle wedges as well as process exits."""
import json
from types import SimpleNamespace

import pytest

from scripts.agentdesk import supervisor


@pytest.mark.parametrize('mode', ['wedged', 'never-ready', 'child-exit', 'shutdown'])
def test_supervision_has_bounded_recovery_and_preserves_stop_intent(monkeypatch, mode):
    children = []
    handlers = {}
    clock = [0]
    probes = []

    class Child:
        def __init__(self, arguments):
            self.arguments = arguments
            self.code = 1 if mode == 'child-exit' and children else None
            children.append(self)
        def poll(self):
            return self.code
        def terminate(self):
            self.code = 0
        def wait(self, timeout=None):
            return self.code

    def probe(revision):
        assert revision == 'a' * 40
        probes.append(clock[0])
        return mode == 'wedged' and len(probes) == 1

    def sleep(seconds):
        clock[0] += seconds
        if mode == 'shutdown':
            handlers[supervisor.signal.SIGTERM](None, None)
        assert clock[0] <= 100, 'Supervision must never wait forever'

    monkeypatch.setattr(supervisor, 'Path', lambda _: SimpleNamespace(read_text=lambda **_: json.dumps({'commit': 'a' * 40})))
    monkeypatch.setattr(supervisor.subprocess, 'Popen', Child)
    monkeypatch.setattr(supervisor.signal, 'signal', lambda key, callback: handlers.update({key: callback}))
    monkeypatch.setattr(supervisor.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(supervisor.time, 'sleep', sleep)
    monkeypatch.setattr(supervisor, 'healthy', probe)
    monkeypatch.setattr(supervisor, 'STARTUP_SECONDS', 3)
    assert supervisor.main() == (0 if mode == 'shutdown' else 1)
    assert len(children) == 2
    assert all(child.poll() is not None for child in children)
    if mode == 'wedged':
        assert len(probes) == 1 + supervisor.FAILED_PROBES
    if mode == 'never-ready':
        assert probes[-1] >= 3
