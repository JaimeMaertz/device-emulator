"""
Enough of extronlib.interface for catalog device modules to run off-processor against the
emulator. The connections are real: EthernetClientInterface is a TCP socket, and SerialInterface
reads and writes a file descriptor (a pty in the tests), so bytes cross a real boundary.

Like the processor: incoming data goes to ReceiveData(interface, data) unless a SendAndWait is
collecting it, in which case SendAndWait returns it.
"""
import os
import re
import socket
import threading
import time


class _Port:
    def _init_port(self):
        self._lock = threading.Lock()
        self._waiting = None      # bytearray while a SendAndWait is collecting
        self._arrived = threading.Condition(self._lock)
        self._running = False

    def _start_reader(self):
        self._running = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        while self._running:
            try:
                data = self._read()
            except OSError:
                data = b''
            if not data:
                if self._running:
                    self._running = False
                    handler = getattr(self, 'Disconnected', None)
                    if callable(handler):
                        handler(self, 'Disconnected')
                break
            with self._lock:
                if self._waiting is not None:
                    self._waiting += data
                    self._arrived.notify_all()
                    continue
            handler = getattr(self, 'ReceiveData', None)
            if callable(handler):
                handler(self, data)

    def Send(self, data):
        if isinstance(data, str):
            data = data.encode('latin-1')
        self._write(data)

    def SendAndWait(self, data, timeout, **delimiter):
        if isinstance(data, str):
            data = data.encode('latin-1')
        deadline = time.monotonic() + timeout
        with self._lock:
            self._waiting = bytearray()
        self._write(data)
        with self._lock:
            try:
                while True:
                    got = bytes(self._waiting)
                    if 'deliTag' in delimiter and delimiter['deliTag'] in got:
                        end = got.index(delimiter['deliTag']) + len(delimiter['deliTag'])
                        return got[:end]
                    if 'deliRex' in delimiter:
                        m = re.search(delimiter['deliRex'], got)
                        if m:
                            return got[:m.end()]
                    if 'deliLen' in delimiter and len(got) >= delimiter['deliLen']:
                        return got[:delimiter['deliLen']]
                    left = deadline - time.monotonic()
                    if left <= 0:
                        # No condition given: everything received by the timeout. A condition
                        # not met: empty (extronlib SendAndWait docstring).
                        return b'' if delimiter else got
                    self._arrived.wait(left)
            finally:
                self._waiting = None


class EthernetClientInterface(_Port):
    def __init__(self, Hostname, IPPort, Protocol='TCP', ServicePort=0, Credentials=None):
        self.Hostname, self.IPPort, self.Protocol = Hostname, IPPort, Protocol
        self.Credentials = Credentials
        self._init_port()
        self._sock = None
        self._ssh = None

    def Connect(self, timeout=None):
        if self._sock:
            return 'ConnectedAlready'
        try:
            if self.Protocol == 'SSH':
                import paramiko
                username, password = self.Credentials or ('admin', '')
                self._ssh = paramiko.SSHClient()
                self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                # no password given (a Tesira's 'default' user): an empty one
                self._ssh.connect(self.Hostname, self.IPPort, username=username,
                                  password='' if password is None else password,
                                  timeout=timeout or 5, allow_agent=False, look_for_keys=False)
                self._sock = self._ssh.invoke_shell()
            else:
                self._sock = socket.create_connection((self.Hostname, self.IPPort), timeout=timeout or 2)
                self._sock.settimeout(None)
        except Exception as e:   # OSError, paramiko.AuthenticationException, SSHException
            self._sock = self._ssh = None
            return str(e) or type(e).__name__
        self._start_reader()
        return 'Connected'

    def Disconnect(self):
        self._running = False
        if self._ssh:
            self._ssh.close()
            self._ssh = self._sock = None
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def _read(self):
        return self._sock.recv(4096)

    def _write(self, data):
        self._sock.sendall(data)


class SerialInterface(_Port):
    """Port is a file descriptor here (one end of a pty pair in the tests)."""

    def __init__(self, Host, Port, Baud=9600, Data=8, Parity='None', Stop=1, FlowControl='Off',
                 CharDelay=0, Mode='RS232'):
        self.Host, self.Port, self.Baud = Host, Port, Baud
        self._init_port()
        self._start_reader()

    def _read(self):
        return os.read(self.Port, 4096)

    def _write(self, data):
        os.write(self.Port, data)

    def Close(self):
        self._running = False
