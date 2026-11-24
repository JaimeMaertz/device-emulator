"""
NEC projectors, binary control protocol (the one the Extron module nec_vp_NP_P547UL_P627UL uses).

Frame: 5 header bytes (byte 4 = data length), data, checksum (sum of the rest, low byte).
The projector only answers; it never sends status on its own. Power has timed warm-up and
cool-down, and power commands during either are refused.

Sources: there is no NEC projector protocol manual on disk. What's here comes from the Extron
module (its command bytes, and its match strings, which were written against real replies) and its
comm sheet (command bytes only). The module's match strings are loose (any `A3` frame parses as
lamp usage), so replies stay within the shapes those strings describe.

Fidelity:
- From the module's match strings (reply layout, not values): running status `20 BF ... 10` +
  16 data bytes (power byte 6, input bytes 8-9, picture mute 11, sound mute 12, freeze 14);
  device status `20 88` + 16; lamp mode `23 B0 .. .. 02 07 <mode>`; lamp usage `23 96` + 10
  (seconds, little-endian, bytes 7-10); volume `23 05 .. .. 10` + 16 (volume byte 12, 0-31);
  error replies `A0`/`A3` + command + 2 bytes + `02` + 2 error bytes.
- Not sourced, best guesses: the error codes used for a refusal (`02 00`); the ID bytes `01 00`
  in replies; a plain 5-byte ack for sets (including data-carrying ones like lamp mode); that
  input / mute / freeze commands are accepted while the projector is off (the remote refuses
  them here); refusing power commands during warm-up / cool-down.
- Warm-up/cool-down defaults are short for testing, not measured. Set the real times in options.
- NORMAL standby (the projector ignores everything while off) is an option, for testing a unit
  whose STANDBY MODE wasn't set to NETWORK STANDBY / SLEEP.
"""
import struct

from ..core import (Device, Button, Choice, Level, Readout, Toggle, NumberOption, FlagOption)

POWER_CODES = {'Off': 0x00, 'Warming Up': 0x01, 'On': 0x04, 'Cooling Down': 0x05}

# Input select: command data byte -> (status bytes the module reads, name)
INPUTS = {
    0xA1: (b'\x01\x21', 'HDMI 1'),
    0xA2: (b'\x02\x21', 'HDMI 2'),
    0x01: (b'\x01\x01', 'Computer'),
    0xBF: (b'\x01\x27', 'HDBaseT'),
    0x1F: (b'\x01\x07', 'USB-A'),
    0x20: (b'\x02\x07', 'LAN'),
}
INPUT_BY_NAME = {name: code for code, (_, name) in INPUTS.items()}

# Single-purpose commands: (byte 0, byte 1) -> (state key, value)
TOGGLES = {
    (0x02, 0x10): ('videoMute', True), (0x02, 0x11): ('videoMute', False),
    (0x02, 0x12): ('audioMute', True), (0x02, 0x13): ('audioMute', False),
}

LAMP_MODES = {0x00: 'Off', 0x03: 'Eco', 0x04: 'Long Life'}

# Device status (00 88) error bytes 5-8, as the module decodes them
DEVICE_STATUS = {
    'Normal': b'\x00\x00\x00\x00', 'Cover Error': b'\x01\x00\x00\x00',
    'Temp Error(Bi-metallic strip)': b'\x02\x00\x00\x00', 'Fan Error': b'\x08\x00\x00\x00',
    'Power Error': b'\x20\x00\x00\x00', 'Lamp Off': b'\x40\x00\x00\x00',
    'Replace Lamp': b'\x80\x00\x00\x00', 'Lamp Life Expired': b'\x00\x01\x00\x00',
}


def checksum(data):
    return sum(data) & 0xFF


def packet(body):
    return bytes(body) + bytes([checksum(body)])


class NecProjector(Device):
    profile_id = 'nec_projector'
    profile_name = 'NEC projector (binary)'
    kind = 'projector'
    models = {
        'NP-P627UL': {},
        'NP-P547UL': {},
    }
    default_tcp_port = 7142
    serial_defaults = {'baudrate': 38400, 'bytesize': 8, 'parity': 'N', 'stopbits': 1}
    options = [
        NumberOption('warmup', 'Warm-up (s)', 8, 0, 600),
        NumberOption('cooldown', 'Cool-down (s)', 5, 0, 600),
        NumberOption('lamp_hours', 'Light source hours at start', 1200, 0, 100000),
        FlagOption('normal_standby', 'NORMAL standby (ignores everything while off)', False),
        FlagOption('start_on', 'Start powered on', False),
    ]
    notes = __doc__

    def reset(self):
        self.state.update(power='On' if self.opts['start_on'] else 'Off', input='HDBaseT',
                          videoMute=False, audioMute=False, freeze=False, volume=15,
                          lampMode='Off', deviceStatus='Normal', lampSeconds=self.opts['lamp_hours'] * 3600)

    def controls(self):
        return [
            Readout('power', 'Power'),
            Button('power_on', 'Power On (remote)'),
            Button('power_off', 'Power Off (remote)'),
            Choice('input', 'Input', INPUT_BY_NAME),
            Toggle('videoMute', 'Picture mute'),
            Toggle('audioMute', 'Sound mute'),
            Toggle('freeze', 'Freeze'),
            Level('volume', 'Volume', 0, 31),
            Choice('lampMode', 'Light mode', LAMP_MODES.values()),
            Choice('deviceStatus', 'Error status', DEVICE_STATUS),
            Readout('lampSeconds', 'Light source hours', get=lambda: self.state['lampSeconds'] // 3600),
        ]

    # ----- power state machine
    def _transition(self, through, to, seconds):
        self.set('power', through)
        self.after('power', seconds, lambda: self.set('power', to))

    def _power(self, on):
        """Returns False if refused (mid-transition)."""
        if self.state['power'] in ('Warming Up', 'Cooling Down'):
            return False
        if on and self.state['power'] == 'Off':
            self._transition('Warming Up', 'On', self.opts['warmup'])
        elif not on and self.state['power'] == 'On':
            self._transition('Cooling Down', 'Off', self.opts['cooldown'])
        return True

    def panel(self, key, value):
        with self.lock:
            if key in ('power_on', 'power_off'):
                if not self._power(key == 'power_on'):
                    self.log('info', 'remote power ignored: {}'.format(self.state['power']))
            elif key == 'deviceStatus':
                self.set(key, value)   # a fault condition, not a remote action
            elif self.state['power'] == 'On':
                self.set(key, value)
            else:
                self.log('info', 'remote {} ignored while {}'.format(key, self.state['power']))

    # ----- protocol
    def frames(self, buffer):
        commands = []
        while len(buffer) >= 6:
            length = 5 + buffer[4] + 1
            if len(buffer) < length:
                break
            cmd, buffer = buffer[:length], buffer[length:]
            if checksum(cmd[:-1]) != cmd[-1]:
                commands.append(None)   # bad checksum: drop it and everything buffered after it
                return commands, b''
            commands.append(cmd)
        return commands, buffer

    def ack(self, cmd):
        return packet([cmd[0] | 0x20, cmd[1], 0x01, 0x00, 0x00])

    def error(self, cmd, err1, err2):
        return packet([cmd[0] | 0xA0, cmd[1], 0x01, 0x00, 0x02, err1, err2])

    def running_status(self):
        s = self.state
        data = bytearray(16)                         # DATA01..DATA16 -> reply bytes 5..20
        data[1] = POWER_CODES[s['power']]             # byte 6: power
        data[2] = 0x00                                # byte 7: signal status (video displayed)
        data[3:5] = INPUTS[INPUT_BY_NAME[s['input']]][0]   # bytes 8-9: input
        data[6] = 0x01 if s['videoMute'] else 0x00    # byte 11: picture mute
        data[7] = 0x01 if s['audioMute'] else 0x00    # byte 12: sound mute
        data[9] = 0x01 if s['freeze'] else 0x00       # byte 14: freeze
        return packet([0x20, 0xBF, 0x01, 0x00, 0x10] + list(data))

    def device_status(self):
        data = bytearray(12)                          # reply bytes 5..16; errors in 5-8
        data[0:4] = DEVICE_STATUS[self.state['deviceStatus']]
        return packet([0x20, 0x88, 0x01, 0x00, 0x0C] + list(data))

    def handle(self, cmd, link):
        s = self.state
        if self.opts['normal_standby'] and s['power'] == 'Off':
            self.log('info', 'in NORMAL standby, ignoring')
            return None
        key = (cmd[0], cmd[1])

        if key == (0x00, 0xBF) and cmd[5:6] == b'\x02':
            return self.running_status()
        if key == (0x00, 0x88):
            return self.device_status()
        if key == (0x03, 0xB0):   # lamp mode read
            code = {v: k for k, v in LAMP_MODES.items()}[s['lampMode']]
            return packet([0x23, 0xB0, 0x01, 0x00, 0x02, 0x07, code])
        if key == (0x03, 0x96):   # lamp usage read: seconds, little-endian
            return packet([0x23, 0x96, 0x01, 0x00, 0x06, 0x00, 0x01] + list(struct.pack('<I', s['lampSeconds'])))
        if key == (0x03, 0x05):   # volume read
            data = bytearray(16)
            data[7] = s['volume']                     # reply byte 12
            return packet([0x23, 0x05, 0x01, 0x00, 0x10] + list(data))

        if key in ((0x02, 0x00), (0x02, 0x01)):
            if not self._power(key == (0x02, 0x00)):
                self.log('info', 'power {} refused: {}'.format('on' if key[1] == 0 else 'off', s['power']))
                return self.error(cmd, 0x02, 0x00)
            return self.ack(cmd)

        if key == (0x02, 0x03) and len(cmd) > 6 and cmd[6] in INPUTS:
            self.set('input', INPUTS[cmd[6]][1])
            return self.ack(cmd)

        if key in TOGGLES:
            self.set(*TOGGLES[key])
            return self.ack(cmd)

        if key == (0x01, 0x98) and len(cmd) > 5:   # freeze
            self.set('freeze', cmd[5] == 0x01)
            return self.ack(cmd)

        if key == (0x03, 0xB1) and len(cmd) > 6 and cmd[6] in LAMP_MODES:
            self.set('lampMode', LAMP_MODES[cmd[6]])
            return self.ack(cmd)

        if key == (0x03, 0x10) and len(cmd) > 8 and cmd[8] <= 31:   # volume set
            self.set('volume', cmd[8])
            return self.ack(cmd)

        # Other sets the module can send (aspect, auto image, menu) are acknowledged without state;
        # other queries go unanswered.
        if cmd[0] in (0x02, 0x03):
            self.log('info', 'acknowledged without state')
            return self.ack(cmd)
        self.log('info', 'no reply (not emulated)')
        return None
