"""
A recording CIP proxy: a real panel (a CCC, a TSW) is pointed at this PC as its control system, and everything is
forwarded to the real processor and written down, both directions, packet by packet.

    python -m crestron_cip record --upstream 192.168.1.10 --out ccc-capture.jsonl

Each line of the JSONL file is one packet: {"t": seconds since start, "conn": connection number, "dir": "panel>cs"
or "cs>panel", "hex": the raw bytes, "text": protocol.describe()}. A line with "event" marks a connection opening or
closing. The panel's own address shows in the "open" line.

TLS: with --tls the proxy listens on 41796 with the certificate and key given and connects upstream over TLS without
checking the processor's certificate. A panel that checks the certificate it is offered will refuse the proxy's;
that refusal is itself the answer to "does it check" and shows as an open immediately followed by a close.
"""
import json
import os
import socket
import ssl
import threading
import time

from . import protocol as p


class RecordingProxy:
    def __init__(self, upstream_host, out_path, listen_port=41794, upstream_port=None, host='0.0.0.0',
                 tls_cert=None, tls_key=None, echo=print):
        self.upstream = (upstream_host, upstream_port or listen_port)
        self.out_path = os.path.abspath(out_path)
        self.out = open(self.out_path, 'a', encoding='utf-8')
        self.echo, self.t0, self.lock = echo, time.monotonic(), threading.Lock()
        self.tls_ctx = None
        if tls_cert:
            self.tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.tls_ctx.load_cert_chain(tls_cert, tls_key)
        from .net import listen
        self.server = listen(host, listen_port)
        self.port = self.server.getsockname()[1]
        self.running, self.count = True, 0
        self.live = {}   # connection number -> (panel socket, threading.Event set when its close is written)

    def serve_forever(self):
        if self.echo:
            self.echo('recording on :{} -> {}:{}, writing {} (Ctrl+C to stop)'.format(self.port, *self.upstream, self.out_path))
        self.server.settimeout(0.5)   # a blocked accept() never sees Ctrl+C on Windows; wake twice a second
        while self.running:
            try:
                conn, addr = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            conn.settimeout(None)
            self.count += 1
            self.live[self.count] = (conn, threading.Event())
            threading.Thread(target=self._session, args=(self.count, conn, addr), daemon=True).start()

    def start(self):
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def _write(self, rec):
        rec['t'] = round(time.monotonic() - self.t0, 3)
        with self.lock:
            if self.out.closed:
                return
            self.out.write(json.dumps(rec) + '\n')
            self.out.flush()
        if self.echo:
            body = rec.get('text') or rec.get('event', '')
            self.echo('{:9.3f} #{} {:9} {}'.format(rec['t'], rec['conn'], rec.get('dir', ''), body))

    def _session(self, n, panel, addr):
        try:
            if self.tls_ctx:
                panel = self.tls_ctx.wrap_socket(panel, server_side=True)
            cs = socket.create_connection(self.upstream, timeout=5)
            if self.tls_ctx:
                ctx = ssl.create_default_context()
                ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
                cs = ctx.wrap_socket(cs, server_hostname=self.upstream[0])
            cs.settimeout(None)
        except (OSError, ssl.SSLError) as e:
            self._write({'conn': n, 'event': 'open failed', 'panel': addr[0], 'error': str(e)})
            panel.close()
            self.live.pop(n, (None, threading.Event()))[1].set()
            return
        self._write({'conn': n, 'event': 'open', 'panel': '{}:{}'.format(*addr), 'upstream': '{}:{}'.format(*self.upstream)})
        done = threading.Event()
        threading.Thread(target=self._pump, args=(n, panel, cs, 'panel>cs', done), daemon=True).start()
        threading.Thread(target=self._pump, args=(n, cs, panel, 'cs>panel', done), daemon=True).start()
        done.wait()
        for s in (panel, cs):
            try:
                s.close()
            except OSError:
                pass
        self._write({'conn': n, 'event': 'close'})
        self.live.pop(n, (None, threading.Event()))[1].set()

    def _pump(self, n, src, dst, direction, done):
        splitter = p.Splitter()
        while not done.is_set():
            try:
                data = src.recv(65536)
            except OSError:
                break
            if not data:
                break
            try:
                dst.sendall(data)
            except OSError:
                break
            for _, _, raw in splitter.feed(data):
                self._write({'conn': n, 'dir': direction, 'hex': raw.hex(), 'text': p.describe(raw)})
        if splitter.buffer:
            self._write({'conn': n, 'dir': direction, 'hex': splitter.buffer.hex(), 'text': 'incomplete tail'})
        done.set()

    def close(self, timeout=3):
        """Stop listening, drop every connection, and close the file once each has written its close line."""
        self.running = False
        try:
            self.server.shutdown(socket.SHUT_RDWR)   # wakes a blocked accept on Linux
        except OSError:
            pass
        self.server.close()
        for conn, _ in list(self.live.values()):
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for _, written in list(self.live.values()):
            written.wait(timeout)
        with self.lock:
            self.out.close()


def read_recording(path):
    """The packets of a recording as (t, conn, dir, raw bytes); events are skipped."""
    out = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            rec = json.loads(line)
            if 'hex' in rec and rec.get('text') != 'incomplete tail':
                out.append((rec['t'], rec['conn'], rec['dir'], bytes.fromhex(rec['hex'])))
    return out
