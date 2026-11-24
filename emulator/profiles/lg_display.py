"""
LG commercial (webOS signage) displays, RS-232 "Set ID" protocol, also on LAN port 9761.

Command:  [Command1][Command2] [SetID] [Data]<CR>        e.g. "ka 01 01\\r"
Reply:    [Command2] [SetID] OK|NG[Data]x                e.g. "a 01 OK01x"
Data "FF" reads the current value. Set ID 00 is broadcast: every display acts, none replies.
Commands for another Set ID are ignored. The display only answers; it never sends status on its own.

Source: LG webOS 6.0/6.1 signage manual (RS-232 chapter,
p.89-123), checked against the Extron module lg_display_xxUH5J_xxUH7J_Series.

Fidelity:
- From the manual: frame and reply format, broadcast/no-ACK, the command set and data values
  below, including the inverted mute (ke 00 = muted, 01 = unmuted) and kd 01 = screen off.
  "When the monitor is turned off, the power key works even in [key lock] On mode."
- Best guesses, to confirm on a real unit (set in options):
  * While off, the manual only says the ACK "is returned properly only when the monitor is fully
    powered on". Here power commands and the power query still answer while off; everything else
    answers NG (or, with the option, not at all).
  * NG replies echo the data that was sent (the last field, for multi-field commands).
  * Commands not emulated, including all multi-field ones (sn, fa, ...), answer NG.
  * A 3-digit Set ID (256+) is echoed as 3 hex digits.
  * Power-on is instant (the manual mentions a possible delay but gives no figure).
"""
import re

from ..core import Device, Button, Choice, Level, Readout, Toggle, NumberOption, ChoiceOption

# xb data -> input name, in the manual's names (p.97). Which codes a display accepts depends on its
# ports; each model lists its own. Anything else gets NG.
UH5J_INPUTS = {   # the codes the Extron xxUH5J/UH7J module sends or reads
    0x90: 'HDMI1 (DTV)', 0xA0: 'HDMI1 (PC)',
    0x91: 'HDMI2 (DTV)', 0xA1: 'HDMI2 (PC)',
    0xC2: 'HDMI3 (DTV)', 0xD2: 'HDMI3 (PC)',
    0xA2: 'OPS/HDMI3/DVI-D (PC)',
    0x96: 'HDMI3/DVI-D (DTV)', 0xA6: 'HDMI3/DVI-D (PC)',
    0xC0: 'DISPLAYPORT (DTV)', 0xD0: 'DISPLAYPORT (PC)',
    0x80: 'DVI-D (DTV)', 0x70: 'DVI-D (PC)',
    0xC1: 'DISPLAYPORT/USB-C (DTV)', 0xD1: 'DISPLAYPORT/USB-C (PC)',
    0xE0: 'SuperSign webOS Player',
}
ASPECTS = {0x02: 'Full Screen', 0x06: 'Original'}

# Simple read/write settings: command -> (state key, allowed data values, data -> state, state -> data)
def _flag(on_value):
    return (lambda d: d == on_value, lambda v: on_value if v else on_value ^ 1)

SIMPLE = {
    b'ke': ('audioMute', {0, 1}) + _flag(0x00),        # 00 = mute (volume off), 01 = mute off
    b'kd': ('screenOff', {0, 1}) + _flag(0x01),        # 00 = screen on, 01 = screen off
    b'km': ('keyLock', {0, 1}) + _flag(0x01),
    b'kl': ('osd', {0, 1}) + _flag(0x01),
    b'kf': ('volume', set(range(0x65)), lambda d: d, lambda v: v),
    b'kc': ('aspect', set(ASPECTS), lambda d: ASPECTS[d], lambda v: {n: c for c, n in ASPECTS.items()}[v]),
}

# IR key codes (mc) that change state (manual IR code table p.87-88); the rest are acknowledged
KEY_POWER, KEY_ON, KEY_OFF, KEY_VOL_UP, KEY_VOL_DOWN, KEY_MUTE = 0x08, 0xC4, 0xC5, 0x02, 0x03, 0x09

COMMAND = re.compile(rb'([a-z])([a-z]) ([0-9a-fA-F]{1,3}) (.+)$')
HEX2 = re.compile(rb'[0-9a-fA-F]{2}$')


class LgDisplay(Device):
    profile_id = 'lg_display'
    profile_name = 'LG signage display (RS-232 Set ID)'
    kind = 'display'
    models = {
        'UH5J / UH7J series (98UH5J-H)': {'inputs': UH5J_INPUTS},
    }
    log_as = 'ascii'
    default_tcp_port = 9761
    serial_defaults = {'baudrate': 9600, 'bytesize': 8, 'parity': 'N', 'stopbits': 1}
    options = [
        NumberOption('set_id', 'Set ID', 1, 1, 1000),
        ChoiceOption('off_reply', 'While off, non-power commands get', ['NG', 'no reply'], 'NG'),
        NumberOption('restart_seconds', 'Restart (ka 02) off time (s)', 3, 0, 120),
    ]
    notes = __doc__

    def reset(self):
        self.inputs = self.spec['inputs']
        self.input_codes = {name: code for code, name in self.inputs.items()}
        self.state.update(power='Off', input=self.inputs[0x90], aspect='Full Screen', volume=20,
                          audioMute=False, screenOff=False, keyLock=False, osd=True)

    def controls(self):
        return [
            Readout('power', 'Power'),
            Button('key_power', 'POWER (remote)'),
            Button('key_on', 'MONITOR ON'),
            Button('key_off', 'MONITOR OFF'),
            Choice('input', 'Input', self.inputs.values()),
            Level('volume', 'Volume', 0, 100),
            Toggle('audioMute', 'Mute'),
            Toggle('screenOff', 'Screen off'),
            Choice('aspect', 'Aspect ratio', ASPECTS.values()),
            Readout('keyLock', 'Key lock (km)'),
        ]

    # ----- front panel / remote
    def panel(self, key, value):
        with self.lock:
            on = self.state['power'] == 'On'
            if key in ('key_power', 'key_on', 'key_off'):
                want = {'key_power': not on, 'key_on': True, 'key_off': False}[key]
                if self.state['keyLock'] and on:
                    self.log('info', 'remote locked (km 01)')
                else:
                    self._set_power(want)
            elif self.state['keyLock']:
                self.log('info', 'remote locked (km 01)')
            elif not on:
                self.log('info', 'remote {} ignored while off'.format(key))
            else:
                self.set(key, value)

    def _set_power(self, on):
        self.cancel('restart')
        self.set('power', 'On' if on else 'Off')

    # ----- protocol
    def frames(self, buffer):
        *lines, rest = buffer.split(b'\r')
        return [line.strip(b'\n ') for line in lines if line.strip(b'\n ')], rest

    def reply(self, cmd2, ok, data):
        return '{} {:02X} {}{:02X}x'.format(cmd2.decode(), self.opts['set_id'], 'OK' if ok else 'NG', data).encode()

    def handle(self, frame, link):
        m = COMMAND.match(frame)
        if not m:
            self.log('info', 'not a Set ID command, ignored')
            return None
        cmd = m.group(1) + m.group(2)
        cmd2 = m.group(2)
        set_id = int(m.group(3), 16)
        broadcast = set_id == 0
        if not broadcast and set_id != self.opts['set_id']:
            return None   # addressed to another display on the chain
        fields = m.group(4).replace(b',', b' ').split()
        if len(fields) != 1 or not HEX2.match(fields[0]):
            # Multi-field commands (sn 0c 01, fa, ...) aren't emulated: NG, like any unsupported one
            self.log('info', 'multi-field command not emulated, NG')
            ok, value = False, int(fields[-1][-2:], 16) if HEX2.match(fields[-1][-2:]) else 0
        else:
            ok, value = self.execute(cmd, int(fields[0], 16))
        if broadcast or ok is None:
            return None
        return self.reply(cmd2, ok, value)

    def execute(self, cmd, data):
        """Returns (ok, data to report), or (None, None) for no reply."""
        s = self.state
        on = s['power'] == 'On'

        if cmd == b'ka':
            if data == 0xFF:
                return True, 1 if on else 0
            if data in (0x00, 0x01):
                self._set_power(data == 0x01)
                return True, data
            if data == 0x02 and on:   # restart
                self.set('power', 'Off')
                self.after('restart', self.opts['restart_seconds'], lambda: self.set('power', 'On'))
                return True, data
            return False, data

        if cmd == b'mc' and data in (KEY_POWER, KEY_ON) and not on:
            self._set_power(True)   # the power key works while off
            return True, data

        if not on:
            self.log('info', 'display off')
            return (None, None) if self.opts['off_reply'] == 'no reply' else (False, data)

        if cmd == b'mc':
            return True, self.key(data)

        if cmd == b'xb':
            if data == 0xFF:
                return True, self.input_codes[s['input']]
            if data in self.inputs:
                self.set('input', self.inputs[data])
                return True, data
            return False, data

        if cmd in SIMPLE:
            key, allowed, decode, encode = SIMPLE[cmd]
            if data == 0xFF:
                return True, encode(s[key])
            if data in allowed:
                self.set(key, decode(data))
                return True, data
            return False, data

        self.log('info', 'command not emulated, NG')
        return False, data

    def key(self, code):
        s = self.state
        if code in (KEY_POWER, KEY_OFF):   # only reached while on
            self._set_power(False)
        elif code == KEY_VOL_UP:
            self.set('volume', min(100, s['volume'] + 1))
        elif code == KEY_VOL_DOWN:
            self.set('volume', max(0, s['volume'] - 1))
        elif code == KEY_MUTE:
            self.set('audioMute', not s['audioMute'])
        else:
            self.log('info', 'IR key {:02X} acknowledged'.format(code))
        return code
