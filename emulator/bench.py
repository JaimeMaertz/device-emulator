"""
A bench is the set of emulated devices for one test setup, saved as JSON in benches/ so a project's
test rig comes back with one load:

    {"devices": [
        {"profile": "nec_projector", "model": "NP-P627UL", "name": "Projector 1",
         "options": {"warmup": 30},
         "transports": [{"type": "tcp", "port": 7142},
                        {"type": "serial", "port": "COM3", "baudrate": 38400}]}
    ]}

Options and models not given take the profile's defaults. A profile that takes a structured
configuration reads it from "design" (a Tesira's blocks: see profiles/biamp_tesira.py).
"start_offline": true loads the device with its Offline fault on, so it opens no port and dials nothing
until Offline is switched off on its card (a stand-in that would clash with a real device on the same network).
"""
import json
import os

from . import profiles  # noqa: F401  (registers every profile)
from .core import PROFILES
from .transports import make_transport

BENCH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'benches')


def build(config):
    """A running device from one bench entry."""
    cls = PROFILES[config['profile']]
    device = cls(config.get('name') or cls.profile_name, config.get('model'), config.get('options'),
                 config.get('design'))
    if config.get('start_offline'):
        device.start_offline = True
        device.set_fault('offline', True)   # before any transport starts, so nothing listens or dials first
    for t in config.get('transports', []):
        device.add_transport(make_transport(device, t))
    return device


def list_benches():
    if not os.path.isdir(BENCH_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(BENCH_DIR) if f.endswith('.json'))


def bench_path(name):
    """A bare name lives in benches/; anything ending in .json is a path."""
    return name if name.endswith('.json') else os.path.join(BENCH_DIR, name + '.json')


def load(name):
    with open(bench_path(name)) as f:
        return json.load(f)['devices']


def save(name, devices):
    os.makedirs(BENCH_DIR, exist_ok=True)
    with open(bench_path(name), 'w') as f:
        json.dump({'devices': [d.to_config() for d in devices]}, f, indent=2)
