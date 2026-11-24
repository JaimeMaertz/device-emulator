"""
NEC large-format displays (E series), external control protocol (RS-232 9600 8N1, LAN TCP 7142).

Frame:  SOH '0' <destination> <source> <message type> <length: 2 ASCII hex, STX..ETX>
        STX <message> ETX <BCC: XOR of every byte after SOH through ETX> CR
The controller sends to the monitor ID ('A' = ID 1 ... 100 = A4h, '*' = all) from source '0';
the display replies to '0' from its own ID. Message types, request -> reply:
    'A' command -> 'B'      'C' get parameter -> 'D'      'E' set parameter -> 'F'
A get/set reply is  STX <result 00|01> <page> <code> <type> <max: 4 hex> <value: 4 hex> ETX,
where result 01 means "unsupported operation with this monitor, or under the current condition".

NULL message (reply 'B', message "BE"): sent for a BCC error, an unsupported message type, a
command it has no answer for, a command left incomplete for 10 s, and for any message received
while it is still executing
Power On / Power Off (the manual has the controller wait 15 s) or an input change (10 s).
The display only answers; it never sends status on its own. On LAN it drops a connection that has
been silent for 15 minutes.

Source: NEC's External_Control_E758_E868_EN_Rev1.0.pdf, checked against Extron's module
nec_display_E758_E868_E988_v1_0_2_0 with Mute and volume updates added.

Fidelity:
- From the manual: framing, BCC, monitor IDs, NULL message and busy windows, power read/control,
  remote keys (C210 -> "C310" "00" key), save settings, timing report, serial / model /
  firmware / MAC reads, input 00-60, aspect 02-70, backlight 00-10.
- Not in this manual: volume 00-62 and mute 00-8D. The Modified module uses them (from a
  third-party reference for other NEC models), and a real E-series unit changed volume with 00-62,
  but blocking on its reply was unreliable. What the real unit replies is the "Volume/mute
  codes" option; confirm on a real unit.
- Model UN552S (a UN-series videowall display): the E-series behavior with the UN module's input
  codes. The Extron module nec_display_UN_UX_Series_v1_0_3_1 sends the same frames, power and
  input opcodes (C203D6, 01D6, 0060), and runs clean against this profile
  (tests/test_nec_display.py section U). Tiling and the UN's other commands aren't emulated.
- Inferred: while not On, parameter get/set answer result 01 ("under current condition"); remote
  keys while off get NULL. Busy windows are the manual's wait times, used as the busy duration.
"""
from ..core import Device, Button, Choice, Level, Readout, Toggle, NumberOption, ChoiceOption

SOH, STX, ETX, CR = 0x01, 0x02, 0x03, 0x0D
POWER_CODES = {'On': 1, 'Standby (Power Save)': 2, 'Off': 4}
NULL = ('B', b'BE')

E_SERIES_INPUTS = {0x11: 'HDMI 1', 0x12: 'HDMI 2', 0x82: 'HDMI 3', 0x01: 'VGA (RGB)',
                   0x0C: 'VGA (YPbPr)', 0x05: 'AV', 0x87: 'Media Player'}
# UN series (videowall displays): the input codes of the Extron module nec_display_UN_UX_Series
# (no UN manual on disk). Same framing and power / input opcodes as the E series.
UN_SERIES_INPUTS = {0x01: 'VGA', 0x03: 'DVI', 0x05: 'Video', 0x0C: 'VGA (YPbPr)', 0x0D: 'Option',
                    0x0F: 'DisplayPort 1', 0x10: 'DisplayPort 2', 0x11: 'HDMI 1', 0x12: 'HDMI 2',
                    0x82: 'HDMI 3', 0x87: 'MP', 0x88: 'Compute Module'}
ASPECTS = {1: 'Normal', 2: 'Full', 4: 'Zoom', 7: '1:1'}

# Remote keys (C210) that change state; every listed key is acknowledged
KEY_VOL_UP, KEY_VOL_DOWN, KEY_MUTE = 0x17, 0x16, 0x1B

VOLUME_MODES = ['supported', 'result 01', 'no reply']


def bcc(data):
    x = 0
    for b in data:
        x ^= b
    return x


def hex_text(text):
    """Serial / model name encoding: each character as two ASCII hex digits."""
    return text.encode().hex().upper().encode()


class NecDisplay(Device):
    profile_id = 'nec_display'
    profile_name = 'NEC large-format display (external control)'
    kind = 'display'
    models = {name: {'inputs': E_SERIES_INPUTS, 'model_name': name} for name in ('E758', 'E868', 'E988')}
    models['UN552S'] = {'inputs': UN_SERIES_INPUTS, 'model_name': 'UN552S'}
    log_as = 'ascii'
    partial_timeout = 10    # manual §5.5.3: timeout error (default 10 s) -> NULL
    default_tcp_port = 7142
    serial_defaults = {'baudrate': 9600, 'bytesize': 8, 'parity': 'N', 'stopbits': 1}
    options = [
        NumberOption('monitor_id', 'Monitor ID', 1, 1, 100),
        NumberOption('busy_power', 'Busy after power on/off (s)', 15, 0, 120),
        NumberOption('busy_input', 'Busy after input change (s)', 10, 0, 120),
        ChoiceOption('volume_mode', 'Volume/mute codes 00-62 / 00-8D (not in manual)', VOLUME_MODES, 'supported'),
        NumberOption('idle_timeout', 'LAN idle disconnect (s, 0 = never)', 900, 0, 86400),
    ]
    notes = __doc__

    def reset(self):
        self.inputs = self.spec['inputs']
        self.input_codes = {name: code for code, name in self.inputs.items()}
        self.tcp_idle_timeout = self.opts['idle_timeout']
        self.state.update(power='Off', busy='', signal=True, input=self.inputs[0x11], volume=30,
                          mute=False, aspect='Full', backlight=70)

    def option_changed(self, key, value):
        if key == 'idle_timeout':
            self.tcp_idle_timeout = value

    def controls(self):
        return [
            Readout('power', 'Power'),
            Readout('busy', 'Busy'),
            Button('power_on', 'Power On (remote)'),
            Button('power_off', 'Power Off (remote)'),
            Button('power_save', 'No signal -> Power Save'),
            Toggle('signal', 'Signal present'),
            Choice('input', 'Input', self.inputs.values()),
            Level('volume', 'Volume', 0, 100),
            Toggle('mute', 'Mute'),
            Level('backlight', 'Backlight', 0, 100),
            Choice('aspect', 'Aspect', ASPECTS.values()),
        ]

    # ----- busy windows: every message gets NULL until they end
    def _busy(self, what, seconds):
        if seconds:
            self.set('busy', what)
            self.after('busy', seconds, lambda: self.set('busy', ''))

    def _power(self, on):
        self.set('power', 'On' if on else 'Off')
        self._busy('power ' + ('on' if on else 'off'), self.opts['busy_power'])

    def _input(self, name):
        self.set('input', name)
        self._busy('input change', self.opts['busy_input'])

    def panel(self, key, value):
        with self.lock:
            if key == 'signal':
                self.set('signal', value)
            elif key == 'power_on':
                self._power(True)
            elif key == 'power_off':
                self._power(False)
            elif key == 'power_save':
                if self.state['power'] == 'On':
                    self.set('power', 'Standby (Power Save)')
            elif self.state['power'] != 'On':
                self.log('info', 'remote {} ignored while {}'.format(key, self.state['power']))
            elif key == 'input':
                self._input(value)
            else:
                self.set(key, value)

    # ----- parameters: (page, code) -> (max, read, write); write returns False if the value is refused
    def parameters(self):
        s = self.state

        def level(key):
            def w(v):
                if not 0 <= v <= 100:
                    return False
                self.set(key, v)
                return True
            return w

        def table(key, values, apply=None):
            def w(v):
                if v not in values:
                    return False
                (apply or (lambda name: self.set(key, name)))(values[v])
                return True
            return w

        params = {
            (0x00, 0x10): (100, lambda: s['backlight'], level('backlight')),
            (0x00, 0x60): (max(self.inputs), lambda: self.input_codes[s['input']],
                           table('input', self.inputs, self._input)),
            (0x02, 0x70): (7, lambda: {n: c for c, n in ASPECTS.items()}[s['aspect']], table('aspect', ASPECTS)),
        }
        if self.opts['volume_mode'] == 'supported':
            params[(0x00, 0x62)] = (100, lambda: s['volume'], level('volume'))
            params[(0x00, 0x8D)] = (2, lambda: 1 if s['mute'] else 2, table('mute', {1: True, 2: False}))
        return params

    # ----- protocol
    def frames(self, buffer):
        """Complete frames, including ones with a bad BCC (the display answers those with NULL).
        A malformed frame is dropped and parsing resyncs on the next SOH after its start. An
        incomplete one waits; after partial_timeout the display answers NULL and clears it."""
        frames = []
        while True:
            start = buffer.find(bytes([SOH]))
            if start < 0:
                return frames, b''
            buffer = buffer[start:]
            if len(buffer) < 7:
                return frames, buffer
            try:
                length = int(buffer[5:7], 16)
            except ValueError:
                length = -1
            total = 7 + length + 2
            if length < 0:
                frames.append(None)
                buffer = buffer[1:]
                continue
            if len(buffer) < total:
                return frames, buffer
            frame = buffer[:total]
            if frame[7] != STX or frame[total - 3] != ETX or frame[-1] != CR:
                frames.append(None)
                buffer = buffer[1:]
                continue
            frames.append(frame)
            buffer = buffer[total:]

    def partial_timed_out(self, link):
        # The manual's NULL cases include "a timeout error has occurred (default 10 sec)"
        return self.message(*NULL)

    def message(self, msg_type, body):
        """Reply frame from the display to the controller."""
        header = bytes([SOH, 0x30, 0x30, 0x40 + self.opts['monitor_id'], ord(msg_type)])
        header += '{:02X}'.format(len(body) + 2).encode()
        data = header + bytes([STX]) + body + bytes([ETX])
        return data + bytes([bcc(data[1:]), CR])

    def handle(self, frame, link):
        dest, msg_type, body = frame[2], chr(frame[4]), frame[8:-3]
        if dest not in (0x40 + self.opts['monitor_id'], 0x2A):
            return None
        if bcc(frame[1:-2]) != frame[-2]:
            self.log('info', 'BCC error -> NULL')
            reply = NULL
        elif self.state['busy']:
            self.log('info', 'busy ({}) -> NULL'.format(self.state['busy']))
            reply = NULL
        else:
            reply = self.execute(msg_type, body)
        if dest == 0x2A or reply is None:   # broadcast: act, don't answer
            return None
        return self.message(*reply)

    def execute(self, msg_type, body):
        if msg_type == 'A':
            return self.command(body)
        if msg_type in ('C', 'E'):
            return self.parameter(msg_type, body)
        self.log('info', 'unsupported message type {} -> NULL'.format(msg_type))
        return NULL

    def command(self, body):
        s = self.state
        if body == b'01D6':
            return 'B', '0200D6000004{:04X}'.format(POWER_CODES[s['power']]).encode()
        if body[:6] == b'C203D6' and len(body) == 10:
            mode = int(body[6:10], 16) if body[6:10].isalnum() else 0
            if mode not in (1, 4):   # 2 and 3 are "do not set"
                return 'B', b'01C203D6' + body[6:10]
            self._power(mode == 1)
            return 'B', b'00C203D6' + body[6:10]
        if body[:4] == b'C210' and len(body) >= 8:
            if s['power'] != 'On':
                self.log('info', 'remote key while {} -> NULL'.format(s['power']))
                return NULL
            self.key(int(body[6:8], 16) if body[6:8].isalnum() else -1)
            return 'B', b'C310' + body[4:8]
        if body == b'0C':
            self.log('info', 'save current settings')
            return 'B', b'000C'
        if body == b'07':
            if s['signal'] and s['power'] == 'On':   # 1080p60: positive syncs, 67.50 kHz, 60.00 Hz
                return 'B', b'4E03' + b'1A5E' + b'1770'
            return 'B', b'4E80' + b'0000' + b'0000'
        if body == b'C216':
            return 'B', b'C316' + hex_text('EMU00001')
        if body == b'C217':
            return 'B', b'C317' + hex_text(self.spec['model_name'])
        if body == b'CA0200':
            return 'B', b'CB020000' + b'R1.000AA'
        if body == b'C22000':
            return 'B', b'C32000' + b'00C0EE123456'
        self.log('info', 'command not emulated -> NULL')
        return NULL

    def key(self, code):
        s = self.state
        if code == KEY_VOL_UP:
            self.set('volume', min(100, s['volume'] + 1))
        elif code == KEY_VOL_DOWN:
            self.set('volume', max(0, s['volume'] - 1))
        elif code == KEY_MUTE:
            self.set('mute', not s['mute'])
        else:
            self.log('info', 'remote key {:02X} acknowledged'.format(code))

    def parameter(self, msg_type, body):
        s = self.state
        page, code = body[0:2], body[2:4]
        reply_type = 'D' if msg_type == 'C' else 'F'
        try:
            key = (int(page, 16), int(code, 16))
        except ValueError:
            return NULL
        unsupported = (reply_type, b'01' + page + code + b'00' + b'0000' + b'0000')
        if key in ((0x00, 0x62), (0x00, 0x8D)) and self.opts['volume_mode'] == 'no reply':
            self.log('info', 'volume/mute code {}-{}: no reply (option)'.format(page.decode(), code.decode()))
            return None
        param = self.parameters().get(key)
        if param is None or s['power'] != 'On':
            why = 'not supported' if param is None else 'display is ' + s['power']
            self.log('info', 'parameter {}-{} -> result 01 ({})'.format(page.decode(), code.decode(), why))
            return unsupported
        maximum, read, write = param
        if msg_type == 'E':
            try:
                value = int(body[4:8], 16)
            except ValueError:
                return unsupported
            if not write(value):
                return reply_type, b'01' + page + code + b'00' + '{:04X}'.format(maximum).encode() + body[4:8]
        return reply_type, b'00' + page + code + b'00' + '{:04X}{:04X}'.format(maximum, read()).encode()
