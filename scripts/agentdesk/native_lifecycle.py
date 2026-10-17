"""Validate a relocated native artifact with real dashboard/gateway processes."""
import argparse
import json
import os
from pathlib import Path
import secrets
import re
import signal
import socket
import subprocess
import tarfile
import tempfile
import time
from urllib import request


def ready(revision, children):
    deadline = time.monotonic() + 300
    evidence = {}
    while time.monotonic() < deadline:
        if any(child.poll() is not None for child in children):
            raise RuntimeError("Native runtime exited during verification")
        for name, url, fields in (
            ('desktop', 'http://127.0.0.1:19219/api/health', ('ok', 'commit')),
            ('gateway', 'http://127.0.0.1:19220/health', ('status',)),
        ):
            try:
                with request.urlopen(url, timeout=3) as response:
                    data = json.load(response)
                # Only public health fields; never echo arbitrary HTTP bodies.
                evidence[name] = {key: data.get(key) for key in fields}
            except (OSError, ValueError) as error:
                evidence[name] = {'error': type(error).__name__}
        desktop, gateway = evidence['desktop'], evidence['gateway']
        if desktop.get('ok') is True and desktop.get('commit') == revision and gateway.get('status') == 'ok':
            return
        time.sleep(2)
    print(json.dumps({'nativeHealthEvidence': evidence}), flush=True)
    raise RuntimeError('Native artifact process revision or health verification failed')


def stop(children, abrupt=False):
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGKILL if abrupt else signal.SIGTERM)  # windows-footgun: ok -- Linux-only hosted lifecycle process groups
        except ProcessLookupError:
            pass
    for child in children:
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)  # windows-footgun: ok -- Linux-only hosted lifecycle process groups
            child.wait(timeout=10)
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGKILL)  # windows-footgun: ok -- Linux-only hosted lifecycle process groups
        except ProcessLookupError:
            pass
    for port in (19219, 19220):
        try:
            connection = socket.create_connection(('127.0.0.1', port), timeout=2)
        except OSError:
            continue
        connection.close()
        raise RuntimeError('Old native runtime survived process-group termination')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('revision')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='native-validation-') as temporary:
        root = Path(temporary)
        payload = root / 'relocated'
        with tarfile.open(args.archive) as archive:
            archive.extractall(payload, filter='data')
        manifest = json.loads((payload / 'manifest.json').read_text(encoding='utf-8-sig'))
        stamp = json.loads((payload / 'hermes-agent/install-stamp.json').read_text(encoding='utf-8-sig'))
        if stamp.get('commit') != args.revision or stamp.get('updateMechanism') != 'external':
            raise RuntimeError('Native artifact identity or update ownership mismatch')
        command = (payload / manifest['runtime']['commands']['hermes']).resolve()
        if not command.is_relative_to(payload):
            raise RuntimeError('Native launcher escapes payload')
        state = root / 'state'
        state.mkdir()
        config = {'platforms': {'api_server': {'enabled': True, 'key': secrets.token_urlsafe(24), 'host': '127.0.0.1', 'port': 19220}}, 'gateway': {'multiplex_profiles': False},
                  'dashboard': {'basic_auth': {'username': 'release-test', 'password': secrets.token_urlsafe(32), 'secret': secrets.token_urlsafe(32)}}}
        (state / 'config.yaml').write_text(json.dumps(config), encoding='utf-8')
        (state / 'preserve.txt').write_text('existing-user-state', encoding='utf-8')
        env = {key: value for key, value in os.environ.items() if key in ('PATH', 'LANG', 'TMPDIR')}
        env.update({'HOME': str(root), 'HERMES_HOME': str(state), 'UV_OFFLINE': '1', 'npm_config_offline': 'true',
                    'HERMES_KANBAN_DISPATCH_IN_GATEWAY': 'false', 'HERMES_BACKGROUND_NOTIFICATIONS': 'off'})
        for cycle in range(3):
            children = []
            try:
                with (root / 'process.log').open('ab') as log:
                    for arguments in [('dashboard', '--host', '127.0.0.1', '--port', '19219', '--no-open', '--skip-build'), ('gateway', 'run')]:
                        children.append(subprocess.Popen([str(command), *arguments], cwd=root, env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True))
                    ready(args.revision, children)
            except Exception:
                # Only synthetic runner state is used here; never host credentials.
                logs = (root / 'process.log').read_text(encoding='utf-8-sig', errors='replace')
                for secret in (config['platforms']['api_server']['key'], config['dashboard']['basic_auth']['password'], config['dashboard']['basic_auth']['secret']):
                    logs = logs.replace(secret, '[redacted-test-credential]')
                logs = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', logs)
                print(json.dumps({'nativeFixtureLogs': logs[-16000:], 'exitCodes': [child.poll() for child in children]}))
                raise
            finally:
                stop(children, abrupt=cycle == 1)
            if (state / 'preserve.txt').read_text(encoding='utf-8-sig') != 'existing-user-state':
                raise RuntimeError('Native restart/recovery changed user state')
        args.output.write_text(json.dumps({'install': True, 'restart': True, 'runningRevision': True, 'health': True, 'recovery': True}), encoding='utf-8')


if __name__ == '__main__':
    main()
