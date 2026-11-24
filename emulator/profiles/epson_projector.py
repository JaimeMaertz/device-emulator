"""
Epson laser projectors over ESC/VP.net (TCP 3629): EB-PU1007B and the rest of the PU100x / PU2010 series, which share
the ESC/VP21 command set.

Session (ESC/VP.net, a 16-byte packet then 18-byte headers):
  request   "ESC/VP.net" 0x10 <type> 00 00 00 <header count> [headers]     type 3 CONNECT, 2 PASSWORD, 1 HELLO
  response  "ESC/VP.net" 0x10 <type> 00 00 <status> 00                      0x20 OK, 0x41 password required,
                                                                              0x43 wrong password, 0x53 busy
On 0x20 to a CONNECT the TCP connection stays open and carries ESC/VP21; on anything else, and after any PASSWORD
reply, the projector closes it. One session at a time. A session idle for 10 minutes is closed.
Commands (ESC/VP21, CR-terminated):  PWR ON   PWR?   SOURCE 30   MUTE ON   ...
Replies:  a set -> ":"   a query -> "PWR=01\r:"   refused -> "ERR\r:"   a bare CR -> ":"

Sources:
- Epson's ESC/VP.net protocol manual: the packet and header layout, the
  status codes, CONNECT and PASSWORD (and that both close the connection on failure, PASSWORD always), session mode on
  TCP 3629, one session, the 10-minute idle cut-off and the ':' answer to a null line.
- Extron's catalog driver epsn_vp_CB_EB_PU100xx_PU2010x_Series_v1_0_2_0, written for these
  models: every command and reply shape below (PWR=00..05/09, SOURCE 10/30/52/53/60/80/A0, MUTE, FREEZE, ASPECT,
  LUMINANCE, LAMP, ONTIME, VOL 0-255, ERR=nn, KEY nn), and that a refused command answers "ERR\r:".
- Field experience with Epson over IP: ERR to most commands in standby and warm-up.

Fidelity:
- From the manual: the session layer, as described above. HELLO is a UDP (session-less) request; over TCP this answers
  0x45 (request not allowed) and closes, which the manual implies but doesn't show.
- From the driver: commands, values and reply shapes. Inputs are the PU100x table (no SDI: PU2010 only); the PU2010
  model adds 60 (SDI).
- Best guesses, not in either source: in standby, only PWR, LAMP?, ONTIME? and ERR? are answered, everything else is
  ERR; during warm-up and cool-down everything but PWR?, LAMP?, ONTIME? and ERR? is ERR (PWR ON while warming up and
  PWR OFF while cooling down included); standby reports PWR=04 (network on, as LAN control requires); the ':' prompt
  is not sent unprompted after CONNECT; a command before CONNECT gets no answer. Warm-up and cool-down times are
  short for testing, not measured (a real laser warm-up is about 30 s).
"""
import struct

from ..core import Device, Button, Choice, Readout, Toggle, NumberOption, TextOption

HEADER = b'ESC/VP.net\x10'
CONNECT, PASSWORD, HELLO = 3, 2, 1
OK, BAD_REQUEST, UNAUTHORIZED, FORBIDDEN, NOT_ALLOWED, BUSY = 0x20, 0x40, 0x41, 0x43, 0x45, 0x53

INPUTS_PU100X = {'10': 'Computer', '30': 'HDMI', '52': 'USB', '53': 'LAN', '80': 'HDBaseT', 'A0': 'DVI-D'}
INPUTS_PU2010 = dict(INPUTS_PU100X, **{'60': 'SDI'})
ASPECTS = ('00', '10', '20', '30', '40', '50', '60', 'A0')
LUMINANCE = ('00', '01', '02', '04', '05')
KEYS = ('03', '05', '16', '35', '36', '37', '38', '4A')
POWER_CODE = {'Off': '04', 'On': '01', 'Warming Up': '02', 'Cooling Down': '03'}
ALWAYS_ANSWERED = ('PWR', 'LAMP', 'ONTIME', 'ERR')


def packet(kind, status, headers=b''):
    count = len(headers) // 18
    return HEADER + bytes([kind, 0, 0, status, count]) + headers


class EpsonProjector(Device):
    profile_id = 'epson_projector'
    profile_name = 'Epson projector (ESC/VP.net, TCP 3629)'
    kind = 'projector'
    models = {'EB-PU1007B': {'inputs': INPUTS_PU100X}, 'EB-PU2010B': {'inputs': INPUTS_PU2010}}
    log_as = 'ascii'
    default_tcp_port = 3629
    options = [
        NumberOption('warmup', 'Warm-up (s)', 8, 0, 600),
        NumberOption('cooldown', 'Cool-down (s)', 5, 0, 600),
        NumberOption('idle_minutes', 'Close an idle session after (min, 0 = never)', 10, 0, 120),
        TextOption('password', 'Password (empty = none; Crestron Connected sets one)', ''),
        NumberOption('hours', 'Light source hours at start', 1500, 0, 100000),
    ]
    notes = __doc__

    def reset(self):
        self.state.update(power='Off', input='30', mute='OFF', freeze='OFF', aspect='00', luminance='00',
                          volume=121, hours=self.opts['hours'], ontime=self.opts['hours'] + 200, error='00')
        self.tcp_idle_timeout = self.opts['idle_minutes'] * 60

    def option_changed(self, key, value):
        if key == 'idle_minutes':
            self.tcp_idle_timeout = value * 60

    def controls(self):
        inputs = self.spec['inputs']
        return [
            Readout('power', 'Power'),
            Button('power_on', 'Power On (remote)'),
            Button('power_off', 'Power Off (remote)'),
            Choice('input', 'Input', list(inputs), labels=inputs),
            Toggle('mute_on', 'A/V mute'),
            Readout('hours', 'Light source hours'),
        ]

    # ----- power state machine
    def _power(self, on):
        """Returns False when refused (mid-transition)."""
        power = self.state['power']
        if power in ('Warming Up', 'Cooling Down'):
            return False
        if on and power == 'Off':
            self.set('power', 'Warming Up')
            self.after('power', self.opts['warmup'], lambda: self.set('power', 'On'))
        elif not on and power == 'On':
            self.set('power', 'Cooling Down')
            self.set('mute', 'OFF')
            self.after('power', self.opts['cooldown'], lambda: self.set('power', 'Off'))
        return True

    def panel(self, key, value):
        with self.lock:
            if key in ('power_on', 'power_off'):
                if not self._power(key == 'power_on'):
                    self.log('info', 'remote power ignored: {}'.format(self.state['power']))
            elif self.state['power'] != 'On':
                self.log('info', 'remote {} ignored while {}'.format(key, self.state['power']))
            elif key == 'mute_on':
                self.set('mute', 'ON' if value else 'OFF')
            else:
                self.set(key, value)

    # ----- framing: ESC/VP.net packets before the session, CR-terminated ESC/VP21 lines after
    def frames(self, buffer):
        frames = []
        while buffer:
            if buffer.startswith(HEADER[:len(buffer)]) or buffer.startswith(HEADER):
                if len(buffer) < 16:
                    return frames, buffer
                size = 16 + buffer[15] * 18
                if len(buffer) < size:
                    return frames, buffer
                frames.append(buffer[:size])
                buffer = buffer[size:]
                continue
            end = buffer.find(b'\r')
            if end < 0:
                return frames, buffer
            frames.append(buffer[:end + 1])
            buffer = buffer[end + 1:]
        return frames, buffer

    def handle(self, frame, link):
        if frame.startswith(HEADER):
            return self._session_request(frame, link)
        if not link.state.get('session'):
            self.log('info', 'ESC/VP21 before CONNECT: ignored (the projector answers only inside a session)', link)
            return None
        return self._command(frame[:-1].decode('ascii', 'replace').strip())

    # ----- ESC/VP.net
    def _password_in(self, frame):
        for i in range(frame[15]):
            ident, attr, info = frame[16 + i * 18], frame[17 + i * 18], frame[18 + i * 18:34 + i * 18]
            if ident == 1:
                return info.rstrip(b'\x00').decode('latin-1') if attr == 1 else ''
        return None

    def _session_request(self, frame, link):
        kind = frame[11]
        if frame[10] != 0x10:
            self.hang_up(link)
            return packet(kind, 0x55)
        if kind == HELLO or kind not in (CONNECT, PASSWORD):
            self.hang_up(link)
            return packet(kind, NOT_ALLOWED if kind == HELLO else BAD_REQUEST)
        wanted = self.opts['password'] or ''
        given = self._password_in(frame)
        if wanted and given is None:
            status = UNAUTHORIZED
        elif wanted and given != wanted:
            status = FORBIDDEN
        else:
            status = OK
        if kind == PASSWORD:
            self.hang_up(link)
            return packet(kind, status if wanted else OK)
        if status == OK and any(other.state.get('session') for other in self.links if other is not link):
            status = BUSY
        if status != OK:
            self.log('info', 'CONNECT refused: 0x{:02X}'.format(status), link)
            self.hang_up(link)
            return packet(kind, status)
        link.state['session'] = True
        self.log('info', 'session established', link)
        return packet(kind, OK)

    # ----- ESC/VP21
    def _command(self, text):
        if not text:
            return b':'
        s = self.state
        word, _, arg = text.upper().partition(' ')
        query = word.endswith('?')
        code = word.rstrip('?')
        power = s['power']

        if code == 'PWR':
            if query:
                return 'PWR={}\r:'.format(POWER_CODE[power]).encode()
            if arg not in ('ON', 'OFF') or not self._power(arg == 'ON'):
                return b'ERR\r:'
            return b':'
        if query and code in ALWAYS_ANSWERED:
            value = {'LAMP': s['hours'], 'ONTIME': s['ontime'], 'ERR': s['error']}[code]
            return '{}={}\r:'.format(code, value).encode()
        if power != 'On':
            self.log('info', '{} refused while {}'.format(text, power))
            return b'ERR\r:'

        settable = {
            'SOURCE': ('input', tuple(self.spec['inputs'])),
            'MUTE': ('mute', ('ON', 'OFF')),
            'FREEZE': ('freeze', ('ON', 'OFF')),
            'ASPECT': ('aspect', ASPECTS),
            'LUMINANCE': ('luminance', LUMINANCE),
        }
        if code in settable:
            key, allowed = settable[code]
            if query:
                return '{}={}\r:'.format(code, s[key]).encode()
            if arg not in allowed:
                return b'ERR\r:'
            self.set(key, arg)
            return b':'
        if code == 'VOL':
            if query:
                return 'VOL={}\r:'.format(s['volume']).encode()
            if arg in ('INC', 'DEC'):
                self.set('volume', max(0, min(255, s['volume'] + (12 if arg == 'INC' else -12))))
                return b':'
            if arg.isdigit() and 0 <= int(arg) <= 255:
                self.set('volume', int(arg))
                return b':'
            return b'ERR\r:'
        if code == 'KEY' and not query and arg in KEYS:
            self.log('info', 'key {} accepted'.format(arg))
            return b':'
        return b'ERR\r:'
