"""
Shared test plumbing: import paths, a pass/fail counter, catalog-module loading, and an emulated
device on a real localhost TCP port. Each test file runs standalone:  python tests/test_x.py
"""
import importlib.util
import os
import socket
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(HERE, 'extronlib_stub'), ROOT]

# Extron's catalog drivers are Extron's code, so they aren't in this repository. Put the .py files you have
# (Global Scripter's module catalog) in tests/drivers/, or point DEVICE_EMULATOR_DRIVERS at the folder that
# holds them; a test whose driver isn't there is skipped.
DRIVERS = os.environ.get('DEVICE_EMULATOR_DRIVERS') or os.path.join(HERE, 'drivers')
SKIPPED = 3  # the exit status run_tests.py reports as skipped

from emulator import profiles  # noqa: E402,F401  (registers every profile)
from emulator.core import PROFILES  # noqa: E402
from emulator.transports import TcpTransport, SshTransport, SerialTransport  # noqa: E402

results = {'ok': 0, 'fail': 0}


def check(name, cond):
    print(('PASS ' if cond else 'FAIL ') + name)
    results['ok' if cond else 'fail'] += 1


def finish():
    print('\n{ok} passed, {fail} failed'.format(**results))
    sys.exit(1 if results['fail'] else 0)


def load_module(filename):
    """Import an Extron catalog driver by its file name, from DRIVERS (searched recursively). When it isn't
    there, the whole test is skipped: its checks drive the emulator through that driver."""
    path = None
    for folder, _, files in os.walk(DRIVERS):
        if filename in files:
            path = os.path.join(folder, filename)
            break
    if path is None:
        print('SKIP: {} not found in {}\n(put Extron\'s catalog driver there, or set DEVICE_EMULATOR_DRIVERS)'
              .format(filename, DRIVERS))
        sys.exit(SKIPPED)
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def start_device(profile_id, name='test', model=None, options=None, port=None, design=None):
    """An emulated device listening on a free localhost port. Returns (device, port)."""
    device = PROFILES[profile_id](name, model, options, design)
    port = port or free_port()
    device.add_transport(TcpTransport(device, port, host='127.0.0.1'))
    return device, port


def add_ssh(device):
    """Give a device an SSH server on a free localhost port too. Returns the port."""
    port = free_port()
    device.add_transport(SshTransport(device, port, host='127.0.0.1'))
    return port


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def refused(port):
    """True if a connection to port is refused. Windows retries a refused connect for ~2 s before
    reporting it, so the timeout has to be longer than that."""
    try:
        socket.create_connection(('127.0.0.1', port), timeout=3).close()
    except ConnectionRefusedError:
        return True
    except OSError:
        return False
    return False


class RawClient:
    """A plain socket to the emulator: send exact bytes, collect whatever comes back."""

    def __init__(self, port):
        self.sock = socket.create_connection(('127.0.0.1', port), timeout=1)

    def send(self, data, wait=0.2):
        self.sock.sendall(data)
        time.sleep(wait)
        return self.read()

    def read(self):
        self.sock.settimeout(0.05)
        reply = b''
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                reply += chunk
        except socket.timeout:
            pass
        return reply

    def read_after(self, seconds):
        time.sleep(seconds)
        return self.read()

    def closed_by_peer(self, timeout=1.0):
        self.sock.settimeout(timeout)
        try:
            return self.sock.recv(1) == b''
        except (socket.timeout, OSError) as e:
            return not isinstance(e, socket.timeout)

    def close(self):
        self.sock.close()
