"""Run the dashboard and loopback API-only gateway inside staging2's limits.

No external messaging platform is enabled by the provisioning script. Docker's
init reaps children. If either process exits, restart the whole fixed service.
"""
import signal
import subprocess
import time

children = []
stopping = False


def stop(_signal=None, _frame=None):
    global stopping
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
    while not stopping and all(child.poll() is None for child in children):
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
raise SystemExit(exit_code)
