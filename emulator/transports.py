"""
How a device is reached: a TCP server (serial-over-IP, or the device's own LAN control port), an
SSH server (a device's SSH control port, e.g. Extron SIS-over-SSH), or a real local COM port wired
to a processor. A device can have several at once, like a real display
with both RS-232 and LAN. Each connection is a Link with its own receive buffer and `state` dict.

Offline (the fault, or booting after a power cycle): the TCP listener closes, so connection
attempts are refused like a device whose control port is down. Existing clients are dropped. A
connect *timeout* (device unplugged from the network) can't be produced from here; that takes a
firewall rule. On a COM port, Offline ignores everything received.
"""
import logging
import socket
import sys
import threading
import time


class Link:
    def __init__(self, label, send, kind):
        self.label, self.send = label, send
        self.kind = kind         # 'tcp' or 'serial', the transport it arrived on
        self.buffer = b''
        self.state = {}          # per-connection protocol state, owned by the profile
        self.partial_timer = None
        self.last_rx = time.monotonic()
        self.close = None        # set by a transport that can hang up on this client (Device.hang_up)


class TcpTransport:
    kind = 'tcp'

    def __init__(self, device, port, host='0.0.0.0'):
        self.device, self.port, self.host = device, int(port), host
        self.server = None
        self.clients = {}   # Link -> socket
        self.error = ''
        self.running = False
        self.offline = False

    def describe(self):
        if self.error:
            return 'TCP :{} (error: {})'.format(self.port, self.error)
        if self.offline:
            return 'TCP :{} (offline: refusing connections)'.format(self.port)
        n = len(self.clients)
        return 'TCP :{} ({} client{})'.format(self.port, n, '' if n == 1 else 's')

    def start(self):
        self.running = True
        if self._listen():
            threading.Thread(target=self._idle_watch, daemon=True).start()

    def _listen(self):
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if sys.platform == 'win32':
                # SO_REUSEADDR on Windows lets a second socket share a listening port silently
                server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen()
        except OSError as e:
            self.error = str(e)
            self.device.log('warn', 'TCP :{} could not listen: {}'.format(self.port, e))
            return False
        self.server, self.error = server, ''
        self.device.log('info', 'listening on TCP :{}'.format(self.port))
        threading.Thread(target=self._accept, args=(server,), daemon=True).start()
        return True

    def _close_listener(self):
        server, self.server = self.server, None
        if server:
            try:
                server.shutdown(socket.SHUT_RDWR)   # wakes a blocked accept() (close alone doesn't on Linux)
            except OSError:
                pass
            server.close()

    def _accept(self, server):
        while self.running and server is self.server:
            try:
                conn, address = server.accept()
            except OSError:
                break
            if not self.running or server is not self.server:
                conn.close()
                break
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self._session, args=(conn, address), daemon=True).start()

    def _session(self, conn, address):
        link = Link('{}:{}'.format(*address), conn.sendall, 'tcp')
        link.close = lambda: self._close(conn)
        self.clients[link] = conn
        self.device.link_opened(link)
        self._serve(link, conn)

    def _serve(self, link, conn, stream=None):
        """Read until the client goes away. `stream` is what to read from, when it isn't the socket
        itself (an SSH channel)."""
        stream = stream or conn
        while self.running:
            try:
                data = stream.recv(4096)
            except OSError:
                break
            if not data:
                break
            link.last_rx = time.monotonic()
            self.device.receive(link, data)
        self.clients.pop(link, None)
        self.device.link_closed(link)
        try:
            conn.close()
        except OSError:
            pass

    def _idle_watch(self):
        while self.running:
            time.sleep(0.5)
            limit = self.device.tcp_idle_timeout
            if not limit:
                continue
            for link, conn in list(self.clients.items()):
                if time.monotonic() - link.last_rx > limit:
                    self.device.log('info', 'idle {}s, closing (as the real device does)'.format(limit), link)
                    self._close(conn)

    @staticmethod
    def _close(conn):
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()

    def drop_clients(self):
        for conn in list(self.clients.values()):
            self._close(conn)

    def set_offline(self, offline):
        if offline == self.offline or not self.running:
            return
        self.offline = offline
        if offline:
            self._close_listener()
            self.drop_clients()
        else:
            self._listen()

    def stop(self):
        self.running = False
        self._close_listener()
        self.drop_clients()

    def to_config(self):
        return {'type': 'tcp', 'port': self.port}


class SshTransport(TcpTransport):
    """An SSH server (password login, one shell channel per connection), like Extron's
    SIS-over-SSH on port 22023. The device decides who may log in (`Device.ssh_login`); what
    crosses the channel is the same byte stream a Telnet client gets. Needs paramiko. The host
    key is generated once per run, so a client that remembers host keys sees a new one each run."""
    kind = 'ssh'
    _host_key = None
    _key_lock = threading.Lock()

    def describe(self):
        return super().describe().replace('TCP :', 'SSH :', 1)

    def start(self):
        try:
            import paramiko  # noqa: F401  (only needed when SSH is used)
        except ImportError:
            self.error = 'SSH needs paramiko (pip install paramiko)'
            self.device.log('warn', self.error)
            return
        # paramiko logs a failed handshake (a client that resets before its banner, say) as an
        # ERROR with a traceback, and with no handler configured Python prints that to the
        # console. _session logs the same failure in the device's log, so paramiko's copy is dropped.
        paramiko_log = logging.getLogger('paramiko')
        if not paramiko_log.handlers:
            paramiko_log.addHandler(logging.NullHandler())
        paramiko_log.propagate = False
        super().start()

    @classmethod
    def host_key(cls):
        import paramiko
        with cls._key_lock:
            if cls._host_key is None:
                cls._host_key = paramiko.RSAKey.generate(2048)
            return cls._host_key

    def _session(self, conn, address):
        import paramiko
        device, label = self.device, '{}:{} ssh'.format(*address)

        shell_started = threading.Event()

        class Server(paramiko.ServerInterface):
            level = None

            # The methods a profile offers (`ssh_auths`, password by default); "none" is never
            # one. SSH clients try "none" first, and an Extron processor whose "none" login
            # succeeds opens a shell but never finishes Connect: it waits out its timeout and
            # drops the session. So a device with no password still takes one (any, even empty),
            # and ssh_login decides.
            def get_allowed_auths(self, username):
                return getattr(device, 'ssh_auths', 'password')

            # Keyboard-interactive, for a profile that lists it in `ssh_auths`: one hidden
            # "Password: " prompt, and the answer goes to ssh_login like a password.
            def check_auth_interactive(self, username, submethods):
                if 'keyboard-interactive' not in getattr(device, 'ssh_auths', 'password'):
                    return paramiko.AUTH_FAILED
                self._interactive_user = username
                query = paramiko.InteractiveQuery('', '')
                query.add_prompt('Password: ', False)
                return query

            def check_auth_interactive_response(self, responses):
                return self._login(getattr(self, '_interactive_user', ''), responses[0] if responses else '')

            # paramiko calls this for a password request whether or not "password" is offered, so
            # a profile that doesn't list it refuses here (the client then falls back, as on the
            # real device).
            def check_auth_password(self, username, password):
                if 'password' not in getattr(device, 'ssh_auths', 'password').split(','):
                    return paramiko.AUTH_FAILED
                return self._login(username, password)

            def _login(self, username, password):
                self.level = device.ssh_login(username, password)
                if self.level:
                    device.log('info', 'SSH login {!r} accepted'.format(username))
                    return paramiko.AUTH_SUCCESSFUL
                device.log('info', 'SSH login {!r} refused'.format(username))
                return paramiko.AUTH_FAILED

            def check_channel_request(self, kind, chanid):
                device.log('info', 'SSH channel request: {}'.format(kind))
                return paramiko.OPEN_SUCCEEDED if kind == 'session' else \
                    paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

            def check_channel_shell_request(self, channel):
                device.log('info', 'SSH shell requested')
                shell_started.set()
                return True

            def check_channel_pty_request(self, *args):
                device.log('info', 'SSH pty requested')
                return True

            def check_channel_exec_request(self, channel, command):
                device.log('info', 'SSH exec requested ({!r}): refused'.format(command))
                return False

            def check_channel_subsystem_request(self, channel, name):
                device.log('info', 'SSH subsystem requested ({!r}): refused'.format(name))
                return False

        server = Server()
        try:
            transport = paramiko.Transport(conn)
            if getattr(device, 'ssh_version', None):
                transport.local_version = device.ssh_version
            transport.add_server_key(self.host_key())
            transport.start_server(server=server)
            channel = transport.accept(20)
        except (paramiko.SSHException, OSError, EOFError) as e:
            device.log('info', '[{}] SSH handshake failed: {}'.format(label, e))
            channel = None
        if channel is None:
            self._close(conn)
            return
        # A device's greeting is shell output, so it goes out once the client has asked for its
        # shell, and after the profile's `ssh_greeting_delay` if it has one. Biamp's Crestron
        # module (v3.3) missed a welcome sent the instant its shell opened: it logged in, sent
        # nothing for 17 s and reconnected; with a one second delay it went on.
        # A profile's `ssh_shell_greeting(address)` is what the shell prints at once (an OpenSSH
        # last-login line), before the delay and the device's own welcome.
        if shell_started.wait(5):
            if hasattr(device, 'ssh_shell_greeting'):
                try:
                    channel.sendall(device.ssh_shell_greeting(address))
                except OSError:
                    pass
            time.sleep(getattr(device, 'ssh_greeting_delay', 0))
        link = Link(label, channel.sendall, 'ssh')
        link.state['ssh_level'] = server.level
        self.clients[link] = conn
        device.link_opened(link)
        self._serve(link, conn, channel)
        transport.close()

    def to_config(self):
        return {'type': 'ssh', 'port': self.port}


class SerialTransport:
    kind = 'serial'

    def __init__(self, device, port, baudrate=9600, bytesize=8, parity='N', stopbits=1):
        self.device, self.port = device, port
        self.baudrate, self.bytesize, self.parity, self.stopbits = int(baudrate), int(bytesize), parity, stopbits
        self.serial = None
        self.link = None
        self.error = ''
        self.running = False

    def settings(self):
        return '{} {}{}{}'.format(self.baudrate, self.bytesize, self.parity, self.stopbits)

    def describe(self):
        if self.error:
            return '{} (error: {})'.format(self.port, self.error)
        if self.device.is_offline():
            return '{} {} (offline: ignoring input)'.format(self.port, self.settings())
        return '{} {}'.format(self.port, self.settings())

    def start(self):
        try:
            import serial   # pyserial; only needed when a COM port is used
            self.serial = serial.Serial(self.port, self.baudrate, bytesize=self.bytesize,
                                        parity=self.parity, stopbits=self.stopbits, timeout=0.05)
        except Exception as e:   # ImportError, SerialException (busy, missing), ValueError
            self.error = str(e)
            self.device.log('warn', '{} could not open: {}'.format(self.port, e))
            return
        self.running, self.error = True, ''
        self.link = Link(self.port, self._write, 'serial')
        self.device.log('info', 'opened {} {}'.format(self.port, self.settings()))
        self.device.link_opened(self.link)
        threading.Thread(target=self._read, daemon=True).start()

    def _write(self, data):
        self.serial.write(data)

    def _read(self):
        while self.running:
            try:
                data = self.serial.read(self.serial.in_waiting or 1)
            except Exception as e:
                if self.running:
                    self.error = str(e)
                    self.device.log('warn', '{} read failed: {}'.format(self.port, e))
                break
            if data:
                self.device.receive(self.link, data)

    def drop_clients(self):
        pass   # a serial line has no connection to drop

    def set_offline(self, offline):
        pass   # Device.receive ignores input while offline

    def stop(self):
        self.running = False
        if self.link:
            self.device.link_closed(self.link)
        if self.serial:
            try:
                self.serial.close()
            except Exception:
                pass

    def to_config(self):
        return {'type': 'serial', 'port': self.port, 'baudrate': self.baudrate,
                'bytesize': self.bytesize, 'parity': self.parity, 'stopbits': self.stopbits}


class CipClientTransport:
    """An outgoing connection: the device dials a control processor (a panel's CIP link to its control system).
    The link behaves as a TCP client's does: the profile's frames() and handle() take what the processor sends, and
    push() sends. Reconnects every `retry` seconds; offline drops the link and stops dialing until back online."""
    kind = 'cip'

    def __init__(self, device, host, port=41794, tls=False, retry=5):
        self.device, self.host, self.port, self.tls, self.retry = device, host, int(port), bool(tls), retry
        self.sock, self.link, self.error = None, None, ''
        self.running = self.offline = False

    def describe(self):
        state = 'offline' if self.offline else 'connected' if self.link else 'dialing' + (
            ' (' + self.error + ')' if self.error else '')
        return 'CIP -> {}:{}{} ({})'.format(self.host, self.port, ' TLS' if self.tls else '', state)

    def start(self):
        self.running = True
        self.offline = self.device.is_offline()   # a device loaded offline never dials before it is switched on
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.running:
            if self.offline:
                time.sleep(0.5)
                continue
            try:
                conn = socket.create_connection((self.host, self.port), timeout=5)
                if self.tls:
                    import ssl
                    ctx = ssl.create_default_context()
                    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
                    conn = ctx.wrap_socket(conn, server_hostname=self.host)
                conn.settimeout(None)
            except OSError as e:
                self.error = str(e)
                time.sleep(self.retry)
                continue
            self.error, self.sock = '', conn
            self.link = Link('{}:{}'.format(self.host, self.port), conn.sendall, 'cip')
            self.device.link_opened(self.link)
            while self.running and not self.offline:
                try:
                    data = conn.recv(65536)
                except OSError:
                    break
                if not data:
                    break
                self.link.last_rx = time.monotonic()
                self.device.receive(self.link, data)
            link, self.link, self.sock = self.link, None, None
            self.device.link_closed(link)
            TcpTransport._close(conn)
            if self.running and not self.offline:
                time.sleep(self.retry)

    def drop_clients(self):
        if self.sock:
            TcpTransport._close(self.sock)

    def set_offline(self, offline):
        self.offline = offline
        if offline:
            self.drop_clients()

    def stop(self):
        self.running = False
        self.drop_clients()

    def to_config(self):
        return {'type': 'cip', 'host': self.host, 'port': self.port, 'tls': self.tls}


def make_transport(device, config):
    if config['type'] == 'cip':
        return CipClientTransport(device, config['host'], config.get('port', 41794), config.get('tls', False))
    if config['type'] == 'tcp':
        return TcpTransport(device, config['port'])
    if config['type'] == 'ssh':
        return SshTransport(device, config['port'])
    return SerialTransport(device, config['port'], config.get('baudrate', 9600),
                           config.get('bytesize', 8), config.get('parity', 'N'), config.get('stopbits', 1))
