"""Exercise the exact exported image on a disposable Docker daemon, no VPS access."""
import argparse
import json
import os
import re
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from urllib import request


def run(*args):
    return subprocess.check_output(args, text=True, encoding="utf-8", timeout=180, stderr=subprocess.PIPE).strip()


def probe(name, revision):
    result = json.loads(run('docker', 'exec', name, 'curl', '-fsS', '--max-time', '3', 'http://127.0.0.1:9119/api/health'))
    gateway = json.loads(run('docker', 'exec', name, 'curl', '-fsS', '--max-time', '3', 'http://127.0.0.1:9120/health'))
    if result.get('ok') is not True or result.get('commit') != revision or gateway.get('status') != 'ok':
        raise RuntimeError('Actual process revision or health mismatch')


def ready(name, revision):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if run('docker', 'inspect', '--format', '{{.State.Running}}', name) != 'true':
            raise RuntimeError('Disposable runtime exited before health verification')
        try:
            probe(name, revision)
            return
        except (subprocess.SubprocessError, ValueError, OSError, RuntimeError):
            time.sleep(2)
    raise RuntimeError('Disposable runtime failed health verification')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('image')
    parser.add_argument('revision')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    name = 'hermes-release-' + secrets.token_hex(6)
    with tempfile.TemporaryDirectory() as temporary:
        state = Path(temporary)
        state.chmod(0o777)
        config = {'platforms': {'api_server': {'enabled': True, 'key': secrets.token_urlsafe(24), 'host': '127.0.0.1', 'port': 9120}},
                  'gateway': {'multiplex_profiles': False}}
        (state / 'config.yaml').write_text(json.dumps(config), encoding='utf-8')
        (state / 'config.yaml').chmod(0o644)
        (state / 'preserve.txt').write_text('existing-user-state', encoding='utf-8')
        try:
            run('docker', 'run', '-d', '--name', name, '--read-only', '--user', '10000:10000',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--pids-limit', '128',
                # no-tmp: ok -- the container's fixed scratch mount, not a host path
                '--memory', '768m', '--cpus', '0.75', '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m',
                '-v', str(state) + ':/state', '-e', 'HERMES_KANBAN_DISPATCH_IN_GATEWAY=false',
                '-e', 'HERMES_BACKGROUND_NOTIFICATIONS=off', '-e', 'HOME=/var/lib/hermes',
                '-e', 'HERMES_RUNTIME_DIR=/var/lib/hermes/.hermes/tools',
                '-e', 'HERMES_WEB_DIST=/var/lib/hermes/.hermes/staging-web-dist',
                '--workdir', '/var/lib/hermes/projects/hermes-agent',
                '--entrypoint', '/var/lib/hermes/.hermes/installs/bcb380a4495ec6cc/environments/f878407c2d4f434794c12650f8fc54c4/venv/bin/python',
                args.image, '/opt/hermes/scripts/agentdesk/supervisor.py')
            ready(name, args.revision)
            first = run('docker', 'inspect', '--format', '{{.State.StartedAt}}', name)
            run('docker', 'restart', name)
            ready(name, args.revision)
            if run('docker', 'inspect', '--format', '{{.State.StartedAt}}', name) == first:
                raise RuntimeError('Restart did not replace the running process')
            # Abrupt worker/runtime loss must recover with the same volume.
            run('docker', 'kill', name)
            run('docker', 'start', name)
            ready(name, args.revision)
            if (state / 'preserve.txt').read_text(encoding="utf-8-sig") != 'existing-user-state':
                raise RuntimeError('User state was not retained')
            args.output.write_text(json.dumps({'install': True, 'restart': True, 'runningRevision': True, 'health': True, 'recovery': True}), encoding='utf-8')
        except Exception:
            status = json.loads(run('docker', 'inspect', name))[0]['State']
            captured = subprocess.run(['docker', 'logs', name], text=True, encoding='utf-8', capture_output=True, check=True, timeout=60)
            logs = captured.stdout + captured.stderr
            classes = sorted(set(re.findall(r'(?m)^([A-Za-z_.]+(?:Error|Exception)):', logs)))
            reasons = [text for text in ('Permission denied', 'Read-only file system', 'Address already in use', 'No module named') if text in logs]
            # This fixture contains generated test credentials only. Redact its key
            # and bound diagnostics so CI failures explain the actual process exit.
            logs = logs.replace(config['platforms']['api_server']['key'], '[redacted-test-key]')
            logs = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', logs)
            print(json.dumps({'fixtureLogs': logs[-16000:]}))
            print(json.dumps({'runtimeExitCode': status['ExitCode'], 'oomKilled': status['OOMKilled'], 'errorClasses': classes, 'reasonClasses': reasons, 'readonlyPaths': sorted(set(re.findall(r"Read-only file system: ['\"]([^'\"]+)", logs))), 'missingModules': sorted(set(re.findall(r"No module named ['\"]([A-Za-z0-9_.]+)", logs)))}))
            raise
        finally:
            run('docker', 'rm', '-f', name)
            # The read-only UID-10000 runtime owns its mutable test-state files.
            # Return this disposable host fixture to the runner for cleanup.
            run('sudo', 'chown', '-R', str(os.getuid()) + ':' + str(os.getgid()), str(state))  # windows-footgun: ok -- fixed Linux hosted-runner fixture cleanup


if __name__ == '__main__':
    main()
