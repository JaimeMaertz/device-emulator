"""
Sockets around the sessions:

    PanelClient    a panel at one IP ID on a real processor, with heartbeats and reconnects (scripts, tests)
    FakeProcessor  a listening processor that accepts given IP IDs (tests, offline benches)

Plain CIP is TCP 41794; secure CIP is TLS on 41796 (tls=True; the processor's certificate is not checked, since a
processor's is self-signed). A 4-series with authentication on still registers plain CIP without a login (measured
on an RMC4).
"""
import socket
import ssl
import threading
import time

from . import protocol as p
from .session import HEARTBEAT_SECONDS, PanelSession, ProcessorSession

PLAIN_PORT, SECURE_PORT = 41794, 41796


def _tls_client(sock, host):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx.wrap_socket(sock, server_hostname=host)


def listen(host, port):
    """A listening socket: SO_REUSEADDR on Linux (a port can be bound again at once after a close), exclusive on
    Windows (where SO_REUSEADDR would let a second socket share the port silently)."""
    import sys
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE if sys.platform == 'win32' else socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen()
    return s


class PanelClient:
    """
        c = PanelClient('192.168.1.50', 0x05).start()
        c.wait_online(10)
        c.press(3); c.release(3)
        c.get('d', 4)
        c.on_join(lambda kind, join, value: print(kind, join, value))
        c.stop()

    `tap(direction, raw)` sees every packet ('rx' / 'tx'). With reconnect=True a dropped connection is retried every
    5 s with the same joins; a refused registration stops it.
    """

    def __init__(self, host, ipid, port=None, tls=False, register_body=p.XPANEL_REGISTER_BODY, reconnect=True, tap=None,
                 device=None):
        self.host, self.ipid, self.tls = host, ipid, tls
        self.port = port or (SECURE_PORT if tls else PLAIN_PORT)
        self.session = PanelSession(ipid, register_body, device)
        self.reconnect, self.tap = reconnect, tap
        self.sock, self.lock, self.running = None, threading.Lock(), False
        self.online = threading.Event()
        self.error = ''

    # ----- life cycle
    def start(self):
        self.running = True
        threading.Thread(target=self._run, name='cip-{:02x}'.format(self.ipid), daemon=True).start()
        return self

    def stop(self):
        self.running = False
        s = self.sock
        if s:
            try:
                s.close()
            except OSError:
                pass

    def wait_online(self, timeout=10):
        return self.online.wait(timeout)

    @property
    def status(self):
        return self.session.status

    def _run(self):
        while self.running:
            try:
                s = socket.create_connection((self.host, self.port), timeout=5)
                if self.tls:
                    s = _tls_client(s, self.host)
                s.settimeout(1)
                self.sock, self.error = s, ''
                self.session.status = 'connecting'
                self.session.update_steps = []
                self._pump(s)
            except OSError as e:
                self.error = str(e)
            self.online.clear()
            self.sock = None
            if not self.running or not self.reconnect or self.session.status == 'refused':
                break
            time.sleep(5)
        self.running = False

    def _pump(self, s):
        splitter, last_tx = p.Splitter(), time.monotonic()
        while self.running:
            try:
                data = s.recv(65536)
                if not data:
                    return
            except socket.timeout:
                data = b''
            for _, _, raw in splitter.feed(data):
                if self.tap:
                    self.tap('rx', raw)
                for out in self.session.receive(raw):
                    self._send(out)
                    last_tx = time.monotonic()
                if self.session.status == 'online':
                    self.online.set()
                if self.session.status in ('refused', 'disconnected'):
                    return
            if time.monotonic() - last_tx > HEARTBEAT_SECONDS and self.session.status != 'connecting':
                self._send(self.session.heartbeat())
                last_tx = time.monotonic()

    def _send(self, raw):
        s = self.sock
        if s is None:
            return False
        with self.lock:
            try:
                s.sendall(raw)
            except OSError:
                return False
        if self.tap:
            self.tap('tx', raw)
        return True

    def send_raw(self, raw):
        """Any packet, as is (for probing what a processor does with one)."""
        return self._send(raw)

    # ----- joins
    def get(self, kind, join, default=None):
        return self.session.get(kind, join, default)

    def on_join(self, fn):
        self.session.on_join(fn)

    def digital(self, join, value, button=False):
        return self._send(self.session.digital(join, value, button))

    def press(self, join):
        return self._send(self.session.press(join))

    def release(self, join):
        return self._send(self.session.release(join))

    def pulse(self, join, seconds=0.2):
        self.press(join)
        time.sleep(seconds)
        return self.release(join)

    def analog(self, join, value):
        return self._send(self.session.analog(join, value))

    def serial(self, join, text):
        return self._send(self.session.serial(join, text))


class FakeProcessor:
    """A processor that listens and accepts `ipids`. Each connection gets a ProcessorSession; `drive(kind, join, value)`
    sets a join on every session (and in the update of later ones). `penultimate_only` stops updates after 0x16."""

    def __init__(self, ipids, port=0, host='127.0.0.1', penultimate_only=False, tap=None):
        self.ipids, self.penultimate_only, self.tap = ipids, penultimate_only, tap
        self.server = listen(host, port)
        self.port = self.server.getsockname()[1]
        self.sessions, self.lock, self.running = {}, threading.Lock(), True
        self.driven = []   # (kind, join, value) set so far, replayed into new sessions
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while self.running:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            sess = ProcessorSession(self.ipids, self.penultimate_only)
            for kind, join, value in self.driven:
                getattr(sess, {'d': 'digital', 'a': 'analog', 's': 'serial'}[kind])(join, value)
            self.sessions[conn] = sess
            threading.Thread(target=self._serve, args=(conn, sess), daemon=True).start()

    def _serve(self, conn, sess):
        self._send(conn, sess.connect())
        splitter = p.Splitter()
        while self.running:
            try:
                data = conn.recv(65536)
            except OSError:
                break
            if not data:
                break
            for _, _, raw in splitter.feed(data):
                if self.tap:
                    self.tap('rx', raw)
                for out in sess.receive(raw):
                    self._send(conn, out)
        self.sessions.pop(conn, None)

    def _send(self, conn, raw):
        with self.lock:
            try:
                conn.sendall(raw)
            except OSError:
                return
        if self.tap:
            self.tap('tx', raw)

    def drive(self, kind, join, value):
        self.driven.append((kind, join, value))
        for conn, sess in list(self.sessions.items()):
            out = getattr(sess, {'d': 'digital', 'a': 'analog', 's': 'serial'}[kind])(join, value)
            if out:
                self._send(conn, out)

    def send_raw(self, ipid, raw):
        """Any packet to the sessions registered at `ipid` (a slot's joins: protocol.slot_serial_from_processor, ...)."""
        for conn, sess in list(self.sessions.items()):
            if sess.ipid == ipid:
                self._send(conn, raw)

    def session(self, ipid):
        """The newest session registered at `ipid`."""
        for sess in reversed(list(self.sessions.values())):
            if sess.ipid == ipid:
                return sess

    def close(self):
        """Stop listening and drop every panel. shutdown() first: on Linux a bare close() wakes neither a blocked
        accept nor a blocked recv, so the port stays held and the panel never sees the drop."""
        self.running = False
        for s in [self.server] + list(self.sessions):
            for step in (lambda: s.shutdown(socket.SHUT_RDWR), s.close):
                try:
                    step()
                except OSError:
                    pass
