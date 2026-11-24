"""
Start the Device Emulator and open its web UI.

    py -3.12 run.py                         empty bench; add devices in the UI
    py -3.12 run.py --bench classroom   load benches/classroom.json
    py -3.12 run.py --port 8081 --no-browser
"""
import argparse
import os
import struct
import sys

if struct.calcsize('P') == 4:
    sys.exit('This is a 32-bit Python ({}). NiceGUI needs a 64-bit Python 3.10-3.13: '
             'run it as  py -3.12 run.py'.format(sys.executable))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from emulator import ui  # noqa: E402
except ImportError as e:
    sys.exit('{}\nInstall the requirements into this Python first:  {} -m pip install -r requirements.txt'
             .format(e, sys.executable))

parser = argparse.ArgumentParser(description='Emulate AV devices for testing control code, with a web UI.')
parser.add_argument('--bench', help='bench to load: a name in benches/, or a path to a .json file')
parser.add_argument('--port', type=int, default=8080, help='web UI port (default 8080)')
parser.add_argument('--host', default=None, help='web UI address (default: all interfaces)')
parser.add_argument('--no-browser', action='store_true', help="don't open a browser tab")
args = parser.parse_args()

if args.bench and not os.path.isfile(ui.bench.bench_path(args.bench)):
    sys.exit('No bench at {}'.format(ui.bench.bench_path(args.bench)))
ui.start(args.bench, port=args.port, show=not args.no_browser, host=args.host)
