"""Validate a relocated native artifact with real dashboard/gateway processes."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile
import tempfile
import time
from urllib import request


def ready(revision):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            with request.urlopen('http://127.0.0.1:19219/api/health', timeout=3) as response:
                desktop = json.load(response)
            with request.urlopen('http://127.0.0.1:19220/health', timeout=3) as response:
                gateway = json.load(response)
            if desktop.get('ok') is True and desktop.get('commit') == revision and gateway.get('status') == 'ok':
                return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('Native artifact process revision or health verification failed')


def stop(children, abrupt=False):
    for child in children:
        if child.poll() is None:
            child.kill() if abrupt else child.terminate()
    for child in children:
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)


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
        command = (payload / manifest['runtime']['commands']['hermes']).resolve()
        if not command.is_relative_to(payload):
            raise RuntimeError('Native launcher escapes payload')
        state = root / 'state'
        state.mkdir()
        config = {'platforms': {'api_server': {'enabled': True, 'key': secrets.token_urlsafe(24), 'host': '127.0.0.1', 'port': 19220}}, 'gateway': {'multiplex_profiles': False}}
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
                        children.append(subprocess.Popen([str(command), *arguments], cwd=root, env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL))
                    ready(args.revision)
            finally:
                stop(children, abrupt=cycle == 1)
            if (state / 'preserve.txt').read_text(encoding='utf-8-sig') != 'existing-user-state':
                raise RuntimeError('Native restart/recovery changed user state')
        args.output.write_text(json.dumps({'install': True, 'restart': True, 'runningRevision': True, 'health': True, 'recovery': True}), encoding='utf-8')


if __name__ == '__main__':
    main()
