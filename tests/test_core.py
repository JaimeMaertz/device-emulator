"""
The shared device core and TCP transport, with a small test profile: connection hooks and per-link
state, unsolicited push, faults (Offline refuses connections, boot time after a power cycle),
exclusive port binding, and that nothing blocks while holding the device lock.
    Run:  python tests/test_core.py
"""
import time
from harness import check, finish, start_device, wait_for, refused, RawClient, TcpTransport
from emulator.core import Device, Button, Readout


class Greeter(Device):
    """Greets each connection, keeps a per-connection counter, and echoes lines."""
    profile_id = 'test_greeter'
    profile_name = 'Test greeter'
    models = {'Test': {}}
    log_as = 'ascii'

    def reset(self):
        self.state.update(count=0, disconnects=0, ticks=0)

    def connected(self, link):
        link.state['n'] = 0
        return b'HELLO\r\n'

    def disconnected(self, link):
        self.state['disconnects'] += 1

    def frames(self, buffer):
        *lines, rest = buffer.split(b'\n')
        return [l for l in lines if l], rest

    def handle(self, frame, link):
        link.state['n'] += 1
        self.set('count', self.state['count'] + 1)
        return b'%s %d\n' % (frame, link.state['n'])

    def controls(self):
        return [Readout('count', 'Count'), Button('announce', 'Announce')]

    def panel(self, key, value):
        with self.lock:
            if key == 'announce':
                self.push(b'NEWS\n')
            elif key == 'tick':
                self.set('ticks', self.state['ticks'] + 1, log=False)


dev, port = start_device('test_greeter')
try:
    a = RawClient(port)
    check('1 a new connection gets the greeting from connected()', a.read_after(0.2) == b'HELLO\r\n')
    b = RawClient(port)
    b.read_after(0.2)
    a.send(b'x\n'); a.send(b'y\n')
    check('2 per-link state: each connection has its own counter',
          a.send(b'z\n') == b'z 3\n' and b.send(b'q\n') == b'q 1\n')
    dev.panel('announce', True)
    check('3 push() reaches every connected link', a.read_after(0.2) == b'NEWS\n' and b.read_after(0.2) == b'NEWS\n')
    b.close()
    check('4 disconnected() runs when a client leaves', wait_for(lambda: dev.state['disconnects'] == 1))

    before = len(dev.entries)
    dev.panel('tick', True)
    check('5 set(log=False) changes state without a log line',
          dev.state['ticks'] == 1 and not any(e[2] == 'state' for e in list(dev.entries)[before:]))

    dev.set_fault('delay', 1.0)
    t0 = time.monotonic()
    dev.panel('announce', True)
    blocked = time.monotonic() - t0
    check('6 a front-panel action is not blocked by the reply delay (output is queued)', blocked < 0.1)
    check('   ...and the push still arrives after the delay', a.read_after(1.3) == b'NEWS\n')
    dev.set_fault('delay', 0)

    dev.set_fault('offline', True)
    check('7 Offline: the connected client is dropped', a.closed_by_peer())
    check('8 Offline: new connections are refused (not accepted-then-closed)', refused(port))
    check('   ...and the card says so', 'refusing' in dev.transports[0].describe())
    dev.set_fault('offline', False)
    c = RawClient(port)
    check('9 back online: connections are accepted and greeted again', c.read_after(0.2) == b'HELLO\r\n')

    dev.set_fault('boot', 4)   # longer than Windows' ~2 s refused-connect retry
    dev.power_cycle()
    check('10 power cycle: connection dropped, refused while booting',
          c.closed_by_peer() and refused(port))
    check('11 after the boot time it accepts again, in its reset state',
          wait_for(lambda: not refused(port), 6) and dev.state['count'] == 0)

    other = Greeter('second')
    other.add_transport(TcpTransport(other, port, host='127.0.0.1'))
    check('12 a second device on the same port gets a bind error (not a silent share)',
          bool(other.transports[0].error))
    other.close()
finally:
    dev.close()

finish()
