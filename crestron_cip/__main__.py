"""
    python -m crestron_cip watch  HOST IPID [--seconds N] [--tls]      register, print every packet and join change
    python -m crestron_cip set    HOST IPID KIND JOIN VALUE [--button]  set one join (d/a/s), then stay 2 s
    python -m crestron_cip press  HOST IPID JOIN [--hold SECONDS]       a button press and release
    python -m crestron_cip record --upstream HOST [--listen PORT] [--out FILE] [--tls --cert F --key F]
    python -m crestron_cip decode FILE.jsonl                            a recording, one packet per line

IPID is hex (03, 0x05). A 4-series answers plain CIP without a login: say so before pointing this at a client's
processor, since anything it sets acts as that panel.
"""
import argparse
import sys
import time

from . import protocol as p
from .net import PanelClient
from .proxy import RecordingProxy, read_recording


def ipid(text):
    return int(text, 16)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='crestron_cip', description='Crestron CIP: watch, set, record, decode.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('watch', 'set', 'press'):
        s = sub.add_parser(name)
        s.add_argument('host')
        s.add_argument('ipid', type=ipid)
        s.add_argument('--tls', action='store_true')
        s.add_argument('--port', type=int)
        if name == 'watch':
            s.add_argument('--seconds', type=float, default=0, help='stop after this long (0 = until Ctrl+C)')
        if name == 'set':
            s.add_argument('kind', choices=['d', 'a', 's'])
            s.add_argument('join', type=int)
            s.add_argument('value')
            s.add_argument('--button', action='store_true')
        if name == 'press':
            s.add_argument('join', type=int)
            s.add_argument('--hold', type=float, default=0.2)
    r = sub.add_parser('record')
    r.add_argument('--upstream', required=True)
    r.add_argument('--upstream-port', type=int)
    r.add_argument('--listen', type=int, default=41794)
    r.add_argument('--out', default='cip-recording.jsonl')
    r.add_argument('--tls', action='store_true', help='listen with TLS (give --cert and --key)')
    r.add_argument('--cert')
    r.add_argument('--key')
    d = sub.add_parser('decode')
    d.add_argument('file')
    a = ap.parse_args(argv)

    if a.cmd == 'decode':
        for t, conn, direction, raw in read_recording(a.file):
            print('{:9.3f} #{} {:9} {}'.format(t, conn, direction, p.describe(raw)))
        return 0

    if a.cmd == 'record':
        if a.tls and not (a.cert and a.key):
            ap.error('--tls needs --cert and --key')
        proxy = RecordingProxy(a.upstream, a.out, a.listen, a.upstream_port, tls_cert=a.cert if a.tls else None,
                               tls_key=a.key)
        print('point the panel at this PC as its control system')
        try:
            proxy.serve_forever()
        except KeyboardInterrupt:
            print('stopping; {} connection(s) recorded in {}'.format(proxy.count, proxy.out_path))
            proxy.close()
        return 0

    def tap(direction, raw):
        if a.cmd == 'watch':
            print('{:>3} {}'.format(direction, p.describe(raw)))

    c = PanelClient(a.host, a.ipid, a.port, a.tls, tap=tap, reconnect=a.cmd == 'watch').start()
    if not c.wait_online(10):
        print('not online after 10 s: status {}, update steps {} {}'.format(c.status, c.session.update_steps, c.error),
              file=sys.stderr)
        if a.cmd != 'watch':
            c.stop()
            return 1
    if a.cmd == 'set':
        value = int(a.value) if a.kind in 'da' else a.value
        {'d': lambda: c.digital(a.join, value, a.button), 'a': lambda: c.analog(a.join, value),
         's': lambda: c.serial(a.join, value)}[a.kind]()
        time.sleep(2)
    elif a.cmd == 'press':
        c.pulse(a.join, a.hold)
        time.sleep(1)
    else:
        try:
            end = time.time() + a.seconds if a.seconds else None
            while end is None or time.time() < end:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
    c.stop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
