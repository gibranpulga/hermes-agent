"""Supervise the fixed dashboard and loopback gateway inside the image.

A live PID alone is insufficient: bounded health failures also terminate this
container, allowing its existing service restart policy to recover both children.
No database or mutable user state is restored by this supervisor.
"""
import json
from pathlib import Path
import signal
import subprocess
import time
from urllib import request

STARTUP_SECONDS = 300
PROBE_SECONDS = 5
FAILED_PROBES = 6


def healthy(revision):
    try:
        with request.urlopen('http://127.0.0.1:9119/api/health', timeout=2) as response:
            desktop = json.load(response)
        with request.urlopen('http://127.0.0.1:9120/health', timeout=2) as response:
            gateway = json.load(response)
        return desktop.get('ok') is True and desktop.get('commit') == revision and gateway.get('status') == 'ok'
    except (OSError, ValueError):
        return False


def main():
    revision = json.loads(Path('/opt/hermes/install-stamp.json').read_text(encoding='utf-8-sig'))['commit']
    children = []
    stopping = False

    def stop(_signal=None, _frame=None):
        nonlocal stopping
        stopping = True
        for child in children:
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for arguments in [('dashboard', '--host', '0.0.0.0', '--port', '9119', '--no-open', '--skip-build'),
                          ('gateway', 'run')]:
            children.append(subprocess.Popen(['/usr/local/bin/hermes', *arguments]))
        deadline = time.monotonic() + STARTUP_SECONDS
        next_probe = 0
        ready = False
        failures = 0
        while not stopping and all(child.poll() is None for child in children):
            now = time.monotonic()
            if now >= next_probe:
                if healthy(revision):
                    ready = True
                    failures = 0
                else:
                    failures += 1
                    if (ready and failures >= FAILED_PROBES) or (not ready and now >= deadline):
                        print('Fixed runtime health checks failed; exiting for service recovery.', flush=True)
                        break
                next_probe = time.monotonic() + PROBE_SECONDS
            time.sleep(1)
        exit_code = 0 if stopping else 1
    finally:
        stop()
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
