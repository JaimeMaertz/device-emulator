"""
Christie GS Series 630 / 635 projectors (DHD630-GS, DWU630-GS), serial API (RS-232 115200 8N1,
LAN TCP 3002).

Message:  (CODE[+SUBC] data)      set        (PWR 1)   (SIN+MAIN 3)
          (CODE[+SUBC]?)          request    (PWR?)    (SIN+MAIN?)
          (#CODE ...)             set with full acknowledgment: echoed back once processed
Reply:    (CODE[+SUBC]!value)     to a request
Error:    (65535 00000 ERRnnnnn "text")
A space between code and data is optional. A '(' before the previous message's ')' discards the
partial message. The projector only answers; it never sends status on its own. A plain set gets
no reply.

Sources: GS Series 630 and 635 Serial Commands, Technical Reference 020-102559-03 Rev. 1, and the Extron module
chri_vp_DHD630_DWU630_GS_v1_0_2_0 (tested by Extron against the projector).

Fidelity:
- From the manual: the message format, '#' acknowledgment, the error shape, TCP 3002 and the
  serial settings, and the codes and values below (PWR 0/1, SHU 0 open / 1 closed, SIN+MAIN
  1 VGA / 3 HDMI 1 / 4 HDMI 2 / 5 DVI-D / 8 HDBaseT).
- Reply values: the manual's format chapter shows a padded reply with text, (PWR!001 "On"), but
  its SHU page shows (SHU!0), and the Extron module only parses the short form ((PWR!1),
  (SIN+MAIN!3), the input optionally zero-padded). Default: the short form. The "Reply format"
  option sends the format chapter's padded form instead, which the module can't parse: use it to
  see what the program does if a real unit answers that way.
- From the module only, not in the manual: input 2 (BNC); aspect presets 0-6 (the manual has
  0-3); closed caption (CLC), freeze (FRZ), PIP position (PPP), PIP size (PHS). Accepted here.
- Best guesses, not in either source: PWR? reads 1 through warm-up and 0 through cool-down; a
  power command during either is refused with an error; while off, sets other than power are
  refused with an error and requests still answer. Warm-up / cool-down defaults are short for testing,
  not measured. The error numbers and texts other than ERR00005 are made up in that shape.
"""
import re

from ..core import Device, Button, Choice, Readout, Toggle, NumberOption, ChoiceOption

INPUTS = {1: 'VGA', 2: 'BNC', 3: 'HDMI 1', 4: 'HDMI 2', 5: 'DVI-D', 8: 'HDBaseT'}
INPUT_BY_NAME = {name: code for code, name in INPUTS.items()}

# Settable codes: code -> (state key, allowed values). Every one can also be requested.
SETTABLE = {
    'PWR': ('power', (0, 1)),
    'SHU': ('shutter', (0, 1)),
    'SIN+MAIN': ('input', tuple(INPUTS)),
    'SIN+PIP': ('pipInput', tuple(INPUTS)),
    'SZP': ('sizePreset', tuple(range(7))),
    'CLC': ('closedCaption', (0, 1, 2)),
    'FRZ': ('freeze', (0, 1)),
    'LPM': ('lightMode', (0, 1, 3)),
    'PIP': ('pip', (0, 1)),
    'PPP': ('pipPosition', tuple(range(8))),
    'PHS': ('pipSize', (0, 1, 2)),
}
READ_ONLY = {'LIF+TPHS': 'hours'}
# Sets with no state (the module's keypad, menu keys, PIP swap): accepted
ACTIONS = {'PPS', 'KEY', 'IRC'}

MESSAGE = re.compile(rb'\((#?)\s*([A-Za-z]{3})(?:\+([A-Za-z0-9]{4}))?\s*(.*)\)', re.S)
REPLY_FORMATS = ['short: (PWR!1) (module, SHU page)', 'padded: (PWR!001 "On") (format chapter)']
POWER_TEXT = {0: 'Off', 1: 'On'}


class ChristieProjector(Device):
    profile_id = 'christie_projector'
    profile_name = 'Christie GS 630 / 635 projector (serial API)'
    kind = 'projector'
    models = {'DWU630-GS': {}, 'DHD630-GS': {}}
    log_as = 'ascii'
    default_tcp_port = 3002
    serial_defaults = {'baudrate': 115200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1}
    options = [
        NumberOption('warmup', 'Warm-up (s)', 8, 0, 600),
        NumberOption('cooldown', 'Cool-down (s)', 5, 0, 600),
        NumberOption('hours', 'Light source hours at start', 1200, 0, 100000),
        ChoiceOption('reply_format', 'Reply format', REPLY_FORMATS, REPLY_FORMATS[0]),
    ]
    notes = __doc__

    def reset(self):
        self.state.update(power='Off', shutter=0, input=8, pipInput=3, sizePreset=0, closedCaption=0,
                          freeze=0, lightMode=0, pip=0, pipPosition=0, pipSize=0, hours=self.opts['hours'])

    def controls(self):
        return [
            Readout('power', 'Power'),
            Button('power_on', 'Power On (remote)'),
            Button('power_off', 'Power Off (remote)'),
            Choice('input', 'Input', INPUTS, labels=INPUTS),
            Toggle('shutter', 'Shutter closed'),
            Readout('hours', 'Light source hours'),
        ]

    # ----- power state machine
    def _power(self, on):
        """Returns False if refused (mid-transition)."""
        power = self.state['power']
        if power in ('Warming Up', 'Cooling Down'):
            return False
        if on and power == 'Off':
            self.set('power', 'Warming Up')
            self.after('power', self.opts['warmup'], lambda: self.set('power', 'On'))
        elif not on and power == 'On':
            self.set('power', 'Cooling Down')
            self.after('power', self.opts['cooldown'], lambda: self.set('power', 'Off'))
        return True

    def panel(self, key, value):
        with self.lock:
            if key in ('power_on', 'power_off'):
                if not self._power(key == 'power_on'):
                    self.log('info', 'remote power ignored: {}'.format(self.state['power']))
            elif self.state['power'] == 'On':
                self.set(key, int(value))
            else:
                self.log('info', 'remote {} ignored while {}'.format(key, self.state['power']))

    # ----- protocol
    def frames(self, buffer):
        frames = []
        while True:
            start = buffer.find(b'(')
            if start < 0:
                return frames, b''
            buffer = buffer[start:]
            end = buffer.find(b')')
            restart = buffer.find(b'(', 1)
            if 0 <= restart and (end < 0 or restart < end):
                buffer = buffer[restart:]     # manual: a new '(' discards the partial message
                continue
            if end < 0:
                return frames, buffer
            frames.append(buffer[:end + 1])
            buffer = buffer[end + 1:]

    def error(self, number, text):
        return '(65535 00000 ERR{:05d} "{}")'.format(number, text).encode()

    def value(self, code):
        """The reply value for a request, in the chosen format."""
        s = self.state
        if code == 'PWR':
            number = 1 if s['power'] in ('On', 'Warming Up') else 0
        elif code in READ_ONLY:
            number = s[READ_ONLY[code]]
        else:
            number = s[SETTABLE[code][0]]
        if self.opts['reply_format'] == REPLY_FORMATS[1] and code == 'PWR':
            return '{:03d} "{}"'.format(number, POWER_TEXT[number])
        if self.opts['reply_format'] == REPLY_FORMATS[1]:
            return '{:03d}'.format(number)
        return str(number)

    def handle(self, frame, link):
        match = MESSAGE.fullmatch(frame)
        if not match:
            return self.error(5, 'Syntax Error')
        ack, code, sub, data = match.groups()
        code = code.decode().upper() + ('+' + sub.decode().upper() if sub else '')
        data = data.decode().strip()

        if data == '?':
            if code not in SETTABLE and code not in READ_ONLY:
                return self.error(1, '{}: Unknown Command'.format(code))
            return '({}!{})'.format(code, self.value(code)).encode()

        if code in ACTIONS:
            self.log('info', '{} accepted without state'.format(code))
            return frame if ack else None
        if code not in SETTABLE:
            return self.error(1, '{}: Unknown Command'.format(code))
        if not data:
            return self.error(5, '{}: Too Few Parameters'.format(code))
        try:
            number = int(data)
        except ValueError:
            return self.error(5, '{}: Invalid Parameter'.format(code))
        key, allowed = SETTABLE[code]
        if number not in allowed:
            return self.error(4, '{}: Value Out Of Range'.format(code))

        if code == 'PWR':
            if not self._power(number == 1):
                self.log('info', 'power refused: {}'.format(self.state['power']))
                return self.error(3, 'PWR: Busy')
        elif self.state['power'] != 'On':
            self.log('info', '{} refused while {}'.format(code, self.state['power']))
            return self.error(3, '{}: Projector Not On'.format(code))
        else:
            self.set(key, number)
        return frame if ack else None
